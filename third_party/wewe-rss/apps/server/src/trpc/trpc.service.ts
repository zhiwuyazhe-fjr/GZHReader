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

type RefreshFailure = Error & {
  reasonCode?: string;
  detail?: string;
};

type RefreshResultPayload = {
  completed: boolean;
  refreshedCount: number;
  totalCount: number;
  budgetRemaining: number;
  reasonCode: string;
  reason: string;
  detail: string;
};

const accountProbeFreshnessWindowMs = 12 * 60 * 60 * 1000;
const reconnectMessage = '删除账号后重新扫码登录';

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

  private createRefreshFailure(
    reasonCode: string,
    reason: string,
    detail: string,
  ): RefreshFailure {
    const error = new Error(reason) as RefreshFailure;
    error.reasonCode = reasonCode;
    error.detail = detail;
    return error;
  }

  private async getAvailabilityFailure(): Promise<RefreshFailure> {
    const allAccounts = (await this.prismaService.account.findMany()) as AccountState[];
    if (allAccounts.length === 0) {
      return this.createRefreshFailure(
        'no_accounts',
        '还没有可用账号',
        '请先去账号页添加一个读书账号',
      );
    }

    const enabledAccounts = allAccounts.filter(
      (account) => account.status === statusMap.ENABLE,
    );
    if (enabledAccounts.length === 0) {
      if (allAccounts.some((account) => account.status === statusMap.DISABLE)) {
        return this.createRefreshFailure(
          'all_disabled',
          '当前没有启用的账号',
          '请先去账号页启用一个账号，再回来刷新',
        );
      }
      return this.createRefreshFailure(
        'relogin_required',
        reconnectMessage,
        reconnectMessage,
      );
    }

    const coolingCount = enabledAccounts.filter((account) =>
      this.isCooling(account),
    ).length;
    const quotaReachedCount = enabledAccounts.filter((account) =>
      this.isQuotaReached(account),
    ).length;

    if (coolingCount > 0 || quotaReachedCount > 0) {
      const detailParts: string[] = [];
      if (coolingCount > 0) {
        detailParts.push(`${coolingCount} 个账号正在暂时休息`);
      }
      if (quotaReachedCount > 0) {
        detailParts.push(`${quotaReachedCount} 个账号已到今天上限`);
      }
      return this.createRefreshFailure(
        'no_available_accounts',
        '这次没能刷新，因为现在没有可用账号',
        detailParts.join('，') || '请稍后再试，或先去账号页检查状态',
      );
    }

    return this.createRefreshFailure(
      'no_available_accounts',
      '这次没能刷新，因为现在没有可用账号',
      '请先去账号页检查状态后再试',
    );
  }

  private describeAccountHealth(account: AccountState) {
    const now = this.getNowUnix();
    const dailyCount = this.getEffectiveDailyCount(account);
    if (account.status === statusMap.INVALID) {
      return {
        healthStatus: 'needs_reauth',
        healthLabel: '需要重新登录',
        healthTone: 'danger',
        healthDetail: account.lastError || reconnectMessage,
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
        healthLabel: '暂时休息中',
        healthTone: 'warning',
        healthDetail: account.lastError
          ? `${account.lastError}，预计 ${until} 后恢复`
          : `系统正在保护这个账号，预计 ${until} 后恢复`,
      };
    }
    if (dailyCount >= accountDailyLimit) {
      return {
        healthStatus: 'cooldown',
        healthLabel: '暂时休息中',
        healthTone: 'warning',
        healthDetail: `今天已经达到 ${accountDailyLimit} 次上限，明天会自动恢复`,
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

  private async markAccountNeedsReauth(id: string) {
    await this.prismaService.account.updateMany({
      where: { id },
      data: {
        consecutiveAuthFailures: authFailureLimit,
        cooldownUntil: null,
        status: statusMap.INVALID,
        lastError: reconnectMessage,
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
    throw await this.getAvailabilityFailure();
  }

  private needsAccountProbe(account: AccountState) {
    if (!account.lastSuccessAt) {
      return true;
    }
    if (account.lastError) {
      return true;
    }
    return Date.now() - account.lastSuccessAt.getTime() >= accountProbeFreshnessWindowMs;
  }

  private async getProbeFeedId(preferredMpId?: string) {
    if (preferredMpId) {
      return preferredMpId;
    }
    const feed = await this.prismaService.feed.findFirst({
      orderBy: [{ syncTime: 'desc' }, { createdAt: 'asc' }],
      select: { id: true },
    });
    return feed?.id || null;
  }

  private async probeAccount(account: AccountState, mpId: string) {
    await this.request.get(`/api/v2/platform/mps/${mpId}/articles`, {
      headers: {
        xid: account.id,
        Authorization: `Bearer ${account.token}`,
      },
      params: { page: 1 },
      timeout: 8 * 1e3,
    });
    await this.recordSuccessfulAccountRequest(account.id);
  }

  private async precheckAccountAvailability(preferredMpId?: string) {
    const budget = await this.getDailyUsageSummary();
    if (budget.used >= ipDailyBudget) {
      throw this.createRefreshFailure(
        'budget_exhausted',
        '今天整体刷新次数已用完',
        '今天的整体刷新额度已经用完，请明天再试',
      );
    }

    const healthyAccounts = await this.getHealthyAccounts();
    if (healthyAccounts.length < 1) {
      throw await this.getAvailabilityFailure();
    }

    const probeMpId = await this.getProbeFeedId(preferredMpId);
    for (const account of healthyAccounts) {
      if (!this.needsAccountProbe(account) || !probeMpId) {
        return account;
      }

      try {
        await this.probeAccount(account, probeMpId);
        return account;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (message.includes('WeReadError401')) {
          continue;
        }
        if (message.includes('WeReadError429') || message.includes('WeReadError400')) {
          continue;
        }

        throw this.createRefreshFailure(
          'refresh_probe_failed',
          '这次没能完成账号检查',
          '请稍后再试，或先去账号页检查状态',
        );
      }
    }

    throw await this.getAvailabilityFailure();
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
          lastError: shouldInvalidate
            ? reconnectMessage
            : '登录状态不稳定，系统让它先休息一会',
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
          lastError: '这个账号今天用得有点多，系统让它休息到明天',
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
        lastError: errMsg.includes('WeReadError400')
          ? '这次请求没有成功，系统稍后会再试'
          : '系统正在短暂保护这个账号，请稍后再试',
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

  async refreshSingleMpArticlesAndUpdateFeed(
    mpId: string,
  ): Promise<RefreshResultPayload> {
    try {
      await this.precheckAccountAvailability(mpId);
      const result = await this.refreshMpArticlesAndUpdateFeed(mpId);
      const summary = await this.getDailyUsageSummary();
      return {
        completed: true,
        refreshedCount: 1,
        totalCount: 1,
        budgetRemaining: summary.remaining,
        reasonCode: '',
        reason: '',
        detail: '',
        ...result,
      };
    } catch (error) {
      const summary = await this.getDailyUsageSummary();
      const failure = error as RefreshFailure;
      return {
        completed: false,
        refreshedCount: 0,
        totalCount: 1,
        budgetRemaining: summary.remaining,
        reasonCode: failure.reasonCode || 'refresh_failed',
        reason: failure.message || '这次刷新没有成功',
        detail: failure.detail || '请稍后再试',
      };
    }
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
        reasonCode: 'already_running',
        reason: '刷新任务已经在运行中',
        detail: '当前已经有一轮刷新在进行，稍后会自动结束',
      };
    }
    const mps = await this.prismaService.feed.findMany();
    this.isRefreshAllMpArticlesRunning = true;
    let refreshedCount = 0;
    let reasonCode = '';
    let reason = '';
    let detail = '';
    try {
      if (mps.length > 0) {
        try {
          await this.precheckAccountAvailability(mps[0].id);
        } catch (error) {
          const failure = error as RefreshFailure;
          reasonCode = failure.reasonCode || 'refresh_failed';
          reason = failure.message || '这次没能完成刷新';
          detail = failure.detail || reconnectMessage;
          return {
            completed: false,
            refreshedCount,
            totalCount: mps.length,
            budgetRemaining: (await this.getDailyUsageSummary()).remaining,
            reasonCode,
            reason,
            detail,
          };
        }
      }
      for (const { id } of mps) {
        const budget = await this.getDailyUsageSummary();
        if (budget.used >= ipDailyBudget) {
          reasonCode = 'budget_exhausted';
          reason = '今天整体刷新次数已用完';
          detail = '今天的整体刷新额度已经用完，请明天再试';
          break;
        }
        try {
          await this.refreshMpArticlesAndUpdateFeed(id);
          refreshedCount += 1;
        } catch (error) {
          const failure = error as RefreshFailure;
          reasonCode = failure.reasonCode || 'refresh_failed';
          reason = failure.message || '刷新过程中出现异常';
          detail = failure.detail || '请稍后再试';
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
      reasonCode,
      reason,
      detail,
    };
  }

  async getMpInfo(url: string) {
    url = url.trim();
    await this.precheckAccountAvailability();
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
