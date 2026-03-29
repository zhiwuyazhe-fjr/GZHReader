import dayjs from 'dayjs';
import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { toast } from 'sonner';
import { useNavigate, useParams } from 'react-router-dom';
import { trpc } from '@web/utils/trpc';
import { returnToWorkspace } from '@web/utils/workspaceReturn';
import ArticleList from './list';

type FeedItem = {
  id: string;
  mpName: string;
  mpCover?: string;
  mpIntro?: string;
  updateTime?: number;
  hasHistory?: number;
};

type RefreshResult = {
  completed?: boolean;
  refreshedCount?: number;
  totalCount?: number;
  budgetRemaining?: number;
  reasonCode?: string;
  reason?: string;
  detail?: string;
};

type FeedMeta = {
  intro: string;
  tags: string[];
};

const tagPattern = /#([^\s#]+)/g;
const reconnectGuide = '删除账号后重新扫码登录';

const parseFeedMeta = (intro?: string): FeedMeta => {
  const source = (intro || '').trim();
  const tags = Array.from(
    new Set(
      Array.from(source.matchAll(tagPattern))
        .map((match) => match[1]?.trim())
        .filter(Boolean),
    ),
  ) as string[];

  const cleanIntro = source
    .replace(tagPattern, '')
    .replace(/\s+/g, ' ')
    .replace(/[，,、]\s*[，,、]/g, '，')
    .trim()
    .replace(/^[，,、\s]+|[，,、\s]+$/g, '');

  return {
    intro: cleanIntro,
    tags,
  };
};

const refreshSummaryText = (result?: RefreshResult) => {
  if (!result) {
    return '这轮刷新已经完成';
  }

  if (result.reasonCode === 'relogin_required') {
    return reconnectGuide;
  }

  if (
    result.reasonCode === 'no_available_accounts' ||
    result.reasonCode === 'no_accounts' ||
    result.reasonCode === 'all_disabled'
  ) {
    return result.detail || '现在没有可用账号，请先去账号页看一下状态';
  }

  if (result.reasonCode === 'budget_exhausted') {
    return result.detail || '今天的整体刷新次数已经用完，请明天再试';
  }

  const refreshedCount = result.refreshedCount ?? 0;
  const totalCount = result.totalCount ?? refreshedCount;
  const reason = result.reason ? `，${result.reason}` : '';
  return `本轮处理 ${refreshedCount} / ${totalCount} 个订阅${reason}`;
};

const normalizeRefreshFailure = (error: unknown) => {
  const message = error instanceof Error ? error.message : '请稍后再试';

  if (
    message.includes(reconnectGuide) ||
    message.includes('当前账号需要重新登录') ||
    message.includes('WeReadError401')
  ) {
    return {
      title: '账号已经失效',
      detail: reconnectGuide,
    };
  }

  if (
    message.includes('没有可用账号') ||
    message.includes('还没有可用账号')
  ) {
    return {
      title: '这次没能刷新，因为现在没有可用账号',
      detail: '请先去账号页检查状态',
    };
  }

  return {
    title: '这次没能完成刷新',
    detail: message,
  };
};

const normalizeImportFailure = (error: unknown) => {
  const message =
    error instanceof Error ? error.message : '请先去账号页看一下账号状态';
  if (
    message.includes('没有可用账号') ||
    message.includes('还没有可用账号') ||
    message.includes(reconnectGuide) ||
    message.includes('重新扫码')
  ) {
    return {
      title: '现在还不能识别这条链接',
      detail: reconnectGuide,
    };
  }
  return {
    title: '现在还没法识别这条链接',
    detail: message,
  };
};

const Feeds = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const utils = trpc.useUtils();

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [wxsLink, setWxsLink] = useState('');
  const [currentMpId, setCurrentMpId] = useState(id || '');

  const {
    refetch: refetchFeedList,
    data: feedData,
    isFetching: isFeedLoading,
  } = trpc.feed.list.useQuery(
    {},
    {
      refetchOnWindowFocus: true,
    },
  );

  const { mutateAsync: getMpInfo, isLoading: isGetMpInfoLoading } =
    trpc.platform.getMpInfo.useMutation({});
  const { mutateAsync: addFeed, isLoading: isAddFeedLoading } =
    trpc.feed.add.useMutation({});
  const { mutateAsync: refreshMpArticles, isLoading: isRefreshLoading } =
    trpc.feed.refreshArticles.useMutation();
  const { mutateAsync: getHistoryArticles, isLoading: isHistoryLoading } =
    trpc.feed.getHistoryArticles.useMutation();
  const { data: inProgressHistoryMp, refetch: refetchInProgressHistoryMp } =
    trpc.feed.getInProgressHistoryMp.useQuery(undefined, {
      refetchOnWindowFocus: true,
      refetchInterval: 10 * 1000,
      refetchOnMount: true,
      refetchOnReconnect: true,
    });
  const { data: isRefreshAllRunning } =
    trpc.feed.isRefreshAllMpArticlesRunning.useQuery();
  const { mutateAsync: deleteFeed, isLoading: isDeleteFeedLoading } =
    trpc.feed.delete.useMutation({});

  useEffect(() => {
    if (!isModalOpen) {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isModalOpen]);

  useEffect(() => {
    setCurrentMpId(id || '');
  }, [id]);

  const items = (feedData?.items || []) as FeedItem[];
  const currentMpInfo = useMemo(
    () => items.find((item) => item.id === currentMpId),
    [currentMpId, items],
  );
  const currentMeta = useMemo(
    () => parseFeedMeta(currentMpInfo?.mpIntro),
    [currentMpInfo?.mpIntro],
  );

  const totalFeeds = items.length;
  const lastUpdatedLabel = currentMpInfo?.updateTime
    ? dayjs(currentMpInfo.updateTime * 1e3).format('YYYY-MM-DD HH:mm')
    : totalFeeds > 0
      ? '按需刷新'
      : '等待接入';

  const handleConfirm = async () => {
    const links = wxsLink
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean);

    if (!links.length) {
      toast.error('先粘贴一条公众号文章分享链接');
      return;
    }

    for (const link of links) {
      let result;
      try {
        result = await getMpInfo({ wxsLink: link });
      } catch (error) {
        const failure = normalizeImportFailure(error);
        toast.warning(failure.title, { description: failure.detail });
        continue;
      }
      if (!result[0]) {
        toast.error('这条链接没有识别成功', {
          description: '请确认它来自公众号文章页',
        });
        continue;
      }

      const item = result[0];
      await addFeed({
        id: item.id,
        mpName: item.name,
        mpCover: item.cover,
        mpIntro: item.intro,
        updateTime: item.updateTime,
        status: 1,
      });

      let refreshResult: RefreshResult | undefined;
      try {
        refreshResult = (await refreshMpArticles({
          mpId: item.id,
        })) as RefreshResult;
      } catch (error) {
        toast.warning('订阅已经接入，等待稍后刷新', {
          description:
            error instanceof Error
              ? error.message
              : '当前暂时没能完成首次刷新',
        });
      }

      if (refreshResult?.completed) {
        toast.success('订阅已经接入并完成首次刷新', {
          description: `${item.name}，${refreshSummaryText(refreshResult)}`,
        });
      } else if (refreshResult) {
        toast.warning('订阅已经接入，等待稍后刷新', {
          description: refreshSummaryText(refreshResult),
        });
      }
    }

    await utils.article.list.reset();
    await refetchFeedList();
    setWxsLink('');
    setIsModalOpen(false);
  };

  const refreshCurrentFeed = async () => {
    if (!currentMpInfo) {
      return;
    }

    let result: RefreshResult;
    try {
      result = (await refreshMpArticles({
        mpId: currentMpInfo.id,
      })) as RefreshResult;
    } catch (error) {
      const failure = normalizeRefreshFailure(error);
      toast.warning(failure.title, {
        description: failure.detail,
      });
      return;
    }
    await refetchFeedList();
    await utils.article.list.reset();
    if (result?.completed === false) {
      if (result.reasonCode === 'relogin_required') {
        toast.warning('账号已经失效', {
          description: reconnectGuide,
        });
        return;
      }
      toast.warning(result.reason || '这次没能刷新', {
        description: refreshSummaryText(result),
      });
      return;
    }
    toast.success('当前订阅已经刷新', {
      description: refreshSummaryText(result),
    });
  };

  const refreshAllFeeds = async () => {
    let result: RefreshResult;
    try {
      result = (await refreshMpArticles({})) as RefreshResult;
    } catch (error) {
      const failure = normalizeRefreshFailure(error);
      toast.warning(failure.title, {
        description: failure.detail,
      });
      return;
    }
    await refetchFeedList();
    await utils.article.list.reset();
    if (result?.completed === false) {
      if (result.reasonCode === 'relogin_required') {
        toast.warning('账号已经失效', {
          description: reconnectGuide,
        });
        return;
      }
      toast.warning(result.reason || '这次没能完成刷新', {
        description: refreshSummaryText(result),
      });
      return;
    }
    toast.success('订阅列表已经刷新', {
      description: refreshSummaryText(result),
    });
  };

  const toggleHistorySync = async () => {
    if (!currentMpInfo) {
      return;
    }
    if (inProgressHistoryMp?.id === currentMpInfo.id) {
      await getHistoryArticles({ mpId: '' });
      toast.success('历史抓取已经停止');
    } else {
      await getHistoryArticles({ mpId: currentMpInfo.id });
      toast.success('历史抓取已经开始');
    }
    await refetchInProgressHistoryMp();
  };

  const removeCurrentFeed = async () => {
    if (!currentMpInfo) {
      return;
    }
    await deleteFeed(currentMpInfo.id);
    await utils.article.list.reset();
    await refetchFeedList();
    setCurrentMpId('');
    navigate('/feeds');
    toast.success('订阅已经移除');
  };

  const renderImportModal =
    isModalOpen && typeof document !== 'undefined'
      ? createPortal(
          <div className="rss-modal-backdrop" onClick={() => setIsModalOpen(false)}>
            <div
              className="rss-modal-card"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="rss-modal-head">
                <div className="rss-stack">
                  <div className="rss-eyebrow">接入入口</div>
                  <h2 className="rss-modal-title">添加新的公众号订阅</h2>
                  <p className="rss-panel-copy">
                    粘贴公众号文章分享链接
                    <br />
                    识别成功后会先保存订阅 能刷新时再补拉文章
                  </p>
                </div>
                <button
                  type="button"
                  className="rss-button rss-modal-close"
                  onClick={() => setIsModalOpen(false)}
                >
                  ×
                </button>
              </div>

              <label className="rss-field">
                <span className="rss-field-label">公众号文章链接</span>
                <textarea
                  className="rss-textarea"
                  value={wxsLink}
                  onChange={(event) => setWxsLink(event.target.value)}
                  placeholder="https://mp.weixin.qq.com/s/..."
                />
              </label>

              <div className="rss-actions">
                <button
                  type="button"
                  className="rss-button is-primary"
                  disabled={isGetMpInfoLoading || isAddFeedLoading}
                  onClick={handleConfirm}
                >
                  {isGetMpInfoLoading || isAddFeedLoading ? '接入中' : '确认接入'}
                </button>
                <button
                  type="button"
                  className="rss-button is-secondary"
                  onClick={() => setIsModalOpen(false)}
                >
                  稍后再说
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      <section className="rss-panel-hero rss-hero-grid">
        <div className="rss-hero-copy">
          <div className="rss-eyebrow">订阅工作台</div>
          <h1 className="rss-title rss-title--single-line">
            把公众号更新整理进同一张编辑桌
          </h1>
          <div className="rss-rule" />
          <p className="rss-copy">
            在这里接入订阅 刷新文章 然后把每天的内容收进日报里
          </p>
          <div className="rss-actions">
            <button
              type="button"
              className="rss-button is-primary"
              onClick={() => setIsModalOpen(true)}
            >
              添加订阅源
            </button>
            <button
              type="button"
              className="rss-button is-secondary"
              disabled={Boolean(isRefreshAllRunning) || isRefreshLoading}
              onClick={refreshAllFeeds}
            >
              {isRefreshAllRunning || isRefreshLoading ? '更新全部中' : '更新全部'}
            </button>
            <button
              type="button"
              className="rss-button is-secondary"
              onClick={returnToWorkspace}
            >
              返回工作台
            </button>
          </div>
        </div>
        <aside className="rss-hero-side">
          <div className="rss-stat-item">
            <div className="rss-stat-label">当前订阅</div>
            <div className="rss-stat-value">{totalFeeds}</div>
          </div>
          <div className="rss-stat-item">
            <div className="rss-stat-label">最近更新时间</div>
            <div className="rss-stat-copy">{lastUpdatedLabel}</div>
          </div>
          <div className="rss-note">
            刷新会先照顾账号状态
            <br />
            没有可用账号时会直接提醒你
          </div>
        </aside>
      </section>

      <div className="rss-page-grid">
        <aside className="rss-sidebar">
          <div className="rss-panel-header">
            <div>
              <h2 className="rss-panel-title">订阅目录</h2>
              <p className="rss-panel-copy">
                共 {totalFeeds} 个订阅
                {isFeedLoading ? '，目录更新中' : '，点开就能查看详情'}
              </p>
            </div>
          </div>
          <div className="rss-sidebar-scroll">
            <div className="rss-list">
            <button
              type="button"
              className={`rss-feed-item${currentMpId === '' ? ' is-active' : ''}`}
              onClick={() => {
                setCurrentMpId('');
                navigate('/feeds');
              }}
            >
              <div className="rss-feed-avatar rss-feed-avatar--placeholder">全</div>
              <div className="rss-feed-name">全部订阅</div>
            </button>

            {!items.length ? (
              <div className="rss-empty">
                还没有订阅源 先粘贴一条公众号文章链接试试
              </div>
            ) : (
              items.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={`rss-feed-item${
                    currentMpId === item.id ? ' is-active' : ''
                  }`}
                  onClick={() => {
                    setCurrentMpId(item.id);
                    navigate(`/feeds/${item.id}`);
                  }}
                >
                  {item.mpCover ? (
                    <img
                      className="rss-feed-avatar"
                      src={item.mpCover}
                      alt={item.mpName}
                    />
                  ) : (
                    <div className="rss-feed-avatar rss-feed-avatar--placeholder">
                      {item.mpName.slice(0, 1)}
                    </div>
                  )}
                  <div className="rss-feed-name">{item.mpName}</div>
                </button>
              ))
            )}
            </div>
          </div>
        </aside>

        <section className="rss-stack">
          <article className="rss-panel">
            <div className="rss-panel-header">
              <div className="rss-feed-detail-header">
                {currentMpInfo ? (
                  currentMpInfo.mpCover ? (
                    <img
                      className="rss-feed-detail-cover"
                      src={currentMpInfo.mpCover}
                      alt={currentMpInfo.mpName}
                    />
                  ) : (
                    <div className="rss-feed-detail-cover rss-feed-avatar--placeholder">
                      {currentMpInfo.mpName.slice(0, 1)}
                    </div>
                  )
                ) : (
                  <div className="rss-feed-detail-cover rss-feed-avatar--placeholder">
                    全
                  </div>
                )}
                <div className="rss-stack">
                  <h2 className="rss-panel-title">
                    {currentMpInfo?.mpName || '全部文章'}
                  </h2>
                  <p className="rss-panel-copy">
                    {currentMpInfo
                      ? currentMeta.intro || '在这里刷新当前订阅，或继续查看文章列表'
                      : '在这里统一刷新订阅，并查看今天收进来的文章'}
                  </p>
                  {currentMeta.tags.length > 0 && (
                    <div className="rss-tag-row">
                      {currentMeta.tags.map((tag) => (
                        <span key={tag} className="rss-chip rss-chip-tag">
                          #{tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
              <div className="rss-actions">
                {currentMpInfo ? (
                  <>
                    <button
                      type="button"
                      className="rss-button is-primary"
                      disabled={isRefreshLoading}
                      onClick={refreshCurrentFeed}
                    >
                      {isRefreshLoading ? '刷新中' : '刷新当前订阅'}
                    </button>
                    {currentMpInfo.hasHistory === 1 && (
                      <button
                        type="button"
                        className="rss-button is-secondary"
                        disabled={isHistoryLoading}
                        onClick={toggleHistorySync}
                      >
                        {inProgressHistoryMp?.id === currentMpInfo.id
                          ? '停止历史抓取'
                          : '补抓历史文章'}
                      </button>
                    )}
                    <button
                      type="button"
                      className="rss-button is-soft-danger"
                      disabled={isDeleteFeedLoading}
                      onClick={removeCurrentFeed}
                    >
                      删除当前订阅
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="rss-button is-primary"
                    disabled={Boolean(isRefreshAllRunning) || isRefreshLoading}
                    onClick={refreshAllFeeds}
                  >
                    {isRefreshAllRunning || isRefreshLoading
                      ? '更新全部中'
                      : '刷新全部订阅'}
                  </button>
                )}
              </div>
            </div>

            <div className="rss-note">
              {currentMpInfo
                ? `最近更新时间 ${lastUpdatedLabel}`
                : '刷新会先照顾账号状态 没有可用账号时会直接提醒你'}
            </div>
          </article>

          <article className="rss-panel">
            <div className="rss-panel-header">
              <div>
                <h2 className="rss-panel-title">文章列表</h2>
                <p className="rss-panel-copy">
                  文章会按发布时间倒序显示 方便你快速确认最新抓取结果
                </p>
              </div>
            </div>
            <ArticleList />
          </article>
        </section>
      </div>
      {renderImportModal}
    </>
  );
};

export default Feeds;
