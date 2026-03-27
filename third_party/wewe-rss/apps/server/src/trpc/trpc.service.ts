import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { ConfigurationType } from '@server/configuration';
import {
  accountDailyLimit,
  authCooldownSeconds,
  authFailureLimit,
  defaultCount,
  ipDailyBudget,
  statusMap,
  transientCooldownSeconds,
} from '@server/constants';
import { PrismaService } from '@server/prisma/prisma.service';
import { TRPCError, initTRPC } from '@trpc/server';
import Axios, { AxiosInstance } from 'axios';
import dayjs from 'dayjs';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';

dayjs.extend(utc);
dayjs.extend(timezone);

type AccountState = {
  id: string;
  token: string;
  name: string;
  status: number;
  consecutiveAuthFailures: number;
  dailyRequestCount: number;
  dailyRequestDate: string | null;
  cooldownUntil: number | null;
  lastError: string | null;
  lastSuccessAt: Date | null;
  createdAt?: Date;
  updatedAt?: Date | null;
};

@Injectable()
export class TrpcService {
  trpc = initTRPC.create();
  publicProcedure = this.trpc.procedure;
  protectedProcedure = this.trpc.procedure.use(({ ctx, next }) => {
    const errorMsg = (ctx as any).errorMsg;
    if (errorMsg) {
      throw new TRPCError({ code: 'UNAUTHORIZED', message: errorMsg });
    }
    return next({ ctx });
  });
  router = this.trpc.router;
  mergeRouters = this.trpc.mergeRouters;
  request: AxiosInstance;
  updateDelayTime = 60;

  inProgressHistoryMp = {
    id: '',
    page: 1,
  };

  isRefreshAllMpArticlesRunning = false;

  private readonly logger = new Logger(this.constructor.name);

  constructor(
    private readonly prismaService: PrismaService,
    private readonly configService: ConfigService,
  ) {
    const { url } =
      this.configService.get<ConfigurationType['platform']>('platform')!;
    this.updateDelayTime =
      this.configService.get<ConfigurationType['feed']>('feed')!.updateDelayTime;

    this.request = Axios.create({ baseURL: url, timeout: 15 * 1e3 });
    this.request.interceptors.response.use(
      (response) => response,
      async (error) => {
        const errMsg = String(error.response?.data?.message || error.message || '');
        const id = String((error.config?.headers as any)?.xid || '');

        if (id) {
          await this.handleAccountRequestError(id, errMsg);
        }

        return Promise.reject(error);
      },
    );
  }

  private getTodayDate() {
    return dayjs.tz(new Date(), 'Asia/Shanghai').format('YYYY-MM-DD');
  }

  private getNowUnix() {
    return Math.floor(Date.now() / 1e3);
  }

  private getNextShanghaiDayUnix() {
    return dayjs.tz(new Date(), 'Asia/Shanghai').add(1, 'day').startOf('day').unix();
  }

  private getEffectiveDailyCount(account: AccountState) {
    return account.dailyRequestDate === this.getTodayDate() ? account.dailyRequestCount : 0;
  }

  private isCooling(account: AccountState) {
    return Boolean(account.cooldownUntil && account.cooldownUntil > this.getNowUnix());
  }

  private isQuotaReached(account: AccountState) {
    return this.getEffectiveDailyCount(account) >= accountDailyLimit;
  }

  private describeAccountHealth(account: AccountState) {
    const now = this.getNowUnix();
    const dailyCount = this.getEffectiveDailyCount(account);
    if (account.status === statusMap.INVALID) {
      return {
        healthStatus: 'needs_reauth',
        healthLabel: '待重登',
        healthTone: 'danger',
        healthDetail: account.lastError || '这个账号需要重新扫码登录',
      };
    }
    if (account.status === statusMap.DISABLE) {
      return {
        healthStatus: 'disabled',
        healthLabel: '禁用',
        healthTone: 'warning',
        healthDetail: '这个账号已被手动禁用',
      };
    }
    if (account.cooldownUntil && account.cooldownUntil > now) {
      const until = dayjs.unix(account.cooldownUntil).format('MM-DD HH:mm');
      return {
        healthStatus: 'cooldown',
        healthLabel: '冷却中',
        healthTone: 'warning',
        healthDetail: account.lastError
          ? `${account.lastError}，冷却至 ${until}`
          : `当前处于冷却中，预计 ${until} 后恢复`,
      };
    }
    if (dailyCount >= accountDailyLimit) {
      return {
        healthStatus: 'cooldown',
        healthLabel: '冷却中',
        healthTone: 'warning',
        healthDetail: `今日请求已达到 ${accountDailyLimit} 次上限，将在明天自动恢复`,
      };
    }
    return {
      healthStatus: 'available',
      healthLabel: '可用',
      healthTone: 'success',
      healthDetail: account.lastSuccessAt
        ? `最近成功请求于 ${dayjs(account.lastSuccessAt).format('MM-DD HH:mm:ss')}`
        : '账号可用于刷新订阅和拉取文章',
    };
  }

  private async resetAccountHealth(id: string) {
    await this.prismaService.account.updateMany({
      where: { id },
      data: {
        consecutiveAuthFailures: 0,
        cooldownUntil: null,
        lastError: null,
      },
    });
  }

  async removeBlockedAccount(id: string) {
    await this.resetAccountHealth(id);
  }

  async listAccountsWithHealth() {
    const accounts = await this.prismaService.account.findMany({
      orderBy: { createdAt: 'asc' },
    });
    return accounts.map((account) => {
      const health = this.describeAccountHealth(account as AccountState);
      const { token: _token, ...safeAccount } = account as AccountState;
      return {
        ...safeAccount,
        dailyRequestCount: this.getEffectiveDailyCount(account as AccountState),
        healthStatus: health.healthStatus,
        healthLabel: health.healthLabel,
        healthTone: health.healthTone,
        healthDetail: health.healthDetail,
      };
    });
  }

  private async getHealthyAccounts() {
    const accounts = (await this.prismaService.account.findMany({
      where: {
        status: statusMap.ENABLE,
      },
    })) as AccountState[];
    return accounts
      .filter((account) => !this.isCooling(account))
      .filter((account) => !this.isQuotaReached(account))
      .sort((left, right) => {
        const leftCount = this.getEffectiveDailyCount(left);
        const rightCount = this.getEffectiveDailyCount(right);
        if (leftCount !== rightCount) {
          return leftCount - rightCount;
        }
        const leftSuccess = left.lastSuccessAt ? left.lastSuccessAt.getTime() : 0;
        const rightSuccess = right.lastSuccessAt ? right.lastSuccessAt.getTime() : 0;
        return leftSuccess - rightSuccess;
      });
  }

  private async getAvailableAccount() {
    const accounts = await this.getHealthyAccounts();
    if (accounts.length > 0) {
      return accounts[0];
    }

    const allAccounts = (await this.prismaService.account.findMany()) as AccountState[];
    if (allAccounts.length === 0) {
      throw new Error('暂无可用读书账号，请先扫码添加账号');
    }
    if (allAccounts.every((account) => account.status === statusMap.INVALID)) {
      throw new Error('当前所有账号都需要重新扫码登录');
    }
    throw new Error('当前所有账号都在冷却中或已达到今日预算，请稍后再试');
  }

  private async recordSuccessfulAccountRequest(id: string) {
    const account = (await this.prismaService.account.findUnique({
      where: { id },
    })) as AccountState | null;
    if (!account) {
      return;
    }
    const nextCount = this.getEffectiveDailyCount(account) + 1;
    await this.prismaService.account.update({
      where: { id },
      data: {
        consecutiveAuthFailures: 0,
        dailyRequestCount: nextCount,
        dailyRequestDate: this.getTodayDate(),
        cooldownUntil: null,
        lastError: null,
        lastSuccessAt: new Date(),
        status: statusMap.ENABLE,
      },
    });
  }

  private async handleAccountRequestError(id: string, errMsg: string) {
    const account = (await this.prismaService.account.findUnique({
      where: { id },
    })) as AccountState | null;
    if (!account) {
      return;
    }

    const now = this.getNowUnix();
    const baseData = {
      lastError: errMsg.slice(0, 1900),
    };

    if (errMsg.includes('WeReadError401')) {
      const nextFailures = (account.consecutiveAuthFailures || 0) + 1;
      const shouldInvalidate = nextFailures >= authFailureLimit;
      await this.prismaService.account.update({
        where: { id },
        data: {
          ...baseData,
          consecutiveAuthFailures: nextFailures,
          cooldownUntil: now + authCooldownSeconds,
          status: shouldInvalidate ? statusMap.INVALID : statusMap.ENABLE,
        },
      });
      this.logger.warn(
        shouldInvalidate
          ? `account ${id} marked invalid after repeated 401`
          : `account ${id} got 401 and entered cooldown`,
      );
      return;
    }

    if (errMsg.includes('WeReadError429')) {
      await this.prismaService.account.update({
        where: { id },
        data: {
          ...baseData,
          cooldownUntil: this.getNextShanghaiDayUnix(),
          dailyRequestCount: accountDailyLimit,
          dailyRequestDate: this.getTodayDate(),
        },
      });
      this.logger.warn(`account ${id} hit 429 and entered day cooldown`);
      return;
    }

    const cooldownSeconds = errMsg.includes('WeReadError400') ? transientCooldownSeconds : 5 * 60;
    await this.prismaService.account.update({
      where: { id },
      data: {
        ...baseData,
        cooldownUntil: now + cooldownSeconds,
      },
    });
    this.logger.warn(`account ${id} entered transient cooldown`);
  }

  async getDailyUsageSummary() {
    const accounts = (await this.prismaService.account.findMany()) as AccountState[];
    const used = accounts.reduce((sum, account) => sum + this.getEffectiveDailyCount(account), 0);
    return {
      used,
      remaining: Math.max(ipDailyBudget - used, 0),
    };
  }

  async getMpArticles(mpId: string, page = 1, retryCount = 3): Promise<{ id: string; title: string; picUrl: string; publishTime: number }[]> {
    const account = await this.getAvailableAccount();

    try {
      const res = await this.request
        .get<
          {
            id: string;
            title: string;
            picUrl: string;
            publishTime: number;
          }[]
        >(`/api/v2/platform/mps/${mpId}/articles`, {
          headers: {
            xid: account.id,
            Authorization: `Bearer ${account.token}`,
          },
          params: {
            page,
          },
        })
        .then((response) => response.data);
      await this.recordSuccessfulAccountRequest(account.id);
      this.logger.log(`getMpArticles(${mpId}) page=${page} articles=${res.length}`);
      return res;
    } catch (err) {
      this.logger.error(`retry(${4 - retryCount}) getMpArticles error`, err as Error);
      if (retryCount > 0) {
        return this.getMpArticles(mpId, page, retryCount - 1);
      }
      throw err;
    }
  }

  async refreshMpArticlesAndUpdateFeed(mpId: string, page = 1) {
    const articles = await this.getMpArticles(mpId, page);

    if (articles.length > 0) {
      let results;
      const { type } =
        this.configService.get<ConfigurationType['database']>('database')!;
      if (type === 'sqlite') {
        const inserts = articles.map(({ id, picUrl, publishTime, title }) =>
          this.prismaService.article.upsert({
            create: { id, mpId, picUrl, publishTime, title },
            update: {
              publishTime,
              title,
            },
            where: { id },
          }),
        );
        results = await this.prismaService.$transaction(inserts);
      } else {
        results = await (this.prismaService.article as any).createMany({
          data: articles.map(({ id, picUrl, publishTime, title }) => ({
            id,
            mpId,
            picUrl,
            publishTime,
            title,
          })),
          skipDuplicates: true,
        });
      }

      this.logger.debug(
        `refreshMpArticlesAndUpdateFeed create results: ${JSON.stringify(results)}`,
      );
    }

    const hasHistory = articles.length < defaultCount ? 0 : 1;

    await this.prismaService.feed.update({
      where: { id: mpId },
      data: {
        syncTime: Math.floor(Date.now() / 1e3),
        hasHistory,
      },
    });

    return { hasHistory };
  }

  async getHistoryMpArticles(mpId: string) {
    if (this.inProgressHistoryMp.id === mpId) {
      this.logger.log(`getHistoryMpArticles(${mpId}) is running`);
      return;
    }

    this.inProgressHistoryMp = {
      id: mpId,
      page: 1,
    };

    if (!this.inProgressHistoryMp.id) {
      return;
    }

    try {
      const feed = await this.prismaService.feed.findFirstOrThrow({
        where: {
          id: mpId,
        },
      });

      if (feed.hasHistory === 0) {
        this.logger.log(`getHistoryMpArticles(${mpId}) has no history`);
        return;
      }

      const total = await this.prismaService.article.count({
        where: {
          mpId,
        },
      });
      this.inProgressHistoryMp.page = Math.ceil(total / defaultCount);

      let i = 1e3;
      while (i-- > 0) {
        if (this.inProgressHistoryMp.id !== mpId) {
          this.logger.log(`getHistoryMpArticles(${mpId}) cancelled`);
          break;
        }
        const budget = await this.getDailyUsageSummary();
        if (budget.remaining < 1) {
          this.logger.warn(`history fetch for ${mpId} stopped because daily budget is exhausted`);
          break;
        }
        const { hasHistory } = await this.refreshMpArticlesAndUpdateFeed(
          mpId,
          this.inProgressHistoryMp.page,
        );
        if (hasHistory < 1) {
          this.logger.log(`getHistoryMpArticles(${mpId}) has no more history`);
          break;
        }
        this.inProgressHistoryMp.page++;

        await new Promise((resolve) =>
          setTimeout(resolve, this.updateDelayTime * 1e3),
        );
      }
    } finally {
      this.inProgressHistoryMp = {
        id: '',
        page: 1,
      };
    }
  }

  async refreshAllMpArticlesAndUpdateFeed() {
    if (this.isRefreshAllMpArticlesRunning) {
      this.logger.log('refreshAllMpArticlesAndUpdateFeed is running');
      return {
        completed: false,
        refreshedCount: 0,
        totalCount: 0,
        budgetRemaining: (await this.getDailyUsageSummary()).remaining,
        reason: '刷新任务已经在运行中',
      };
    }
    const mps = await this.prismaService.feed.findMany();
    this.isRefreshAllMpArticlesRunning = true;
    let refreshedCount = 0;
    let reason = '';
    try {
      for (const { id } of mps) {
        const budget = await this.getDailyUsageSummary();
        if (budget.used >= ipDailyBudget) {
          reason = '已达到今日刷新预算上限，剩余订阅将留待稍后继续';
          break;
        }
        try {
          await this.refreshMpArticlesAndUpdateFeed(id);
          refreshedCount += 1;
        } catch (error) {
          reason = error instanceof Error ? error.message : '刷新过程中出现异常';
          break;
        }

        await new Promise((resolve) =>
          setTimeout(resolve, this.updateDelayTime * 1e3),
        );
      }
    } finally {
      this.isRefreshAllMpArticlesRunning = false;
    }

    const summary = await this.getDailyUsageSummary();
    return {
      completed: refreshedCount === mps.length && !reason,
      refreshedCount,
      totalCount: mps.length,
      budgetRemaining: summary.remaining,
      reason,
    };
  }

  async getMpInfo(url: string) {
    url = url.trim();
    const account = await this.getAvailableAccount();

    const results = await this.request
      .post<
        {
          id: string;
          cover: string;
          name: string;
          intro: string;
          updateTime: number;
        }[]
      >(
        `/api/v2/platform/wxs2mp`,
        { url },
        {
          headers: {
            xid: account.id,
            Authorization: `Bearer ${account.token}`,
          },
        },
      )
      .then((res) => res.data);

    await this.recordSuccessfulAccountRequest(account.id);
    return results;
  }

  async createLoginUrl() {
    return this.request
      .get<{
        uuid: string;
        scanUrl: string;
      }>(`/api/v2/login/platform`)
      .then((res) => res.data);
  }

  async getLoginResult(id: string) {
    return this.request
      .get<{
        message: string;
        vid?: number;
        token?: string;
        username?: string;
      }>(`/api/v2/login/platform/${id}`, { timeout: 120 * 1e3 })
      .then((res) => res.data);
  }
}
