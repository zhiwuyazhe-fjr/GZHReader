import dayjs from 'dayjs';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { useNavigate, useParams } from 'react-router-dom';
import { serverOriginUrl } from '@web/utils/env';
import { trpc } from '@web/utils/trpc';
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
  reason?: string;
};

const refreshSummaryText = (result?: RefreshResult) => {
  if (!result) {
    return '刷新已经完成';
  }

  const refreshedCount = result.refreshedCount ?? 0;
  const totalCount = result.totalCount ?? refreshedCount;
  const budgetRemaining =
    typeof result.budgetRemaining === 'number'
      ? `，剩余预算 ${result.budgetRemaining} 次`
      : '';
  const reason = result.reason ? `。${result.reason}` : '';

  return `本轮处理 ${refreshedCount} / ${totalCount} 个订阅${budgetRemaining}${reason}`;
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
    setCurrentMpId(id || '');
  }, [id]);

  const items = (feedData?.items || []) as FeedItem[];
  const currentMpInfo = useMemo(
    () => items.find((item) => item.id === currentMpId),
    [currentMpId, items],
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
      const result = await getMpInfo({ wxsLink: link });
      if (!result[0]) {
        toast.error('这条分享链接没有识别成功', {
          description:
            '请确认它来自公众号文章页，并且完整复制了 https://mp.weixin.qq.com/s/... 链接',
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

      const refreshResult = (await refreshMpArticles({
        mpId: item.id,
      })) as RefreshResult;

      toast.success('订阅已经加入并完成首轮刷新', {
        description: `${item.name}。${refreshSummaryText(refreshResult)}`,
      });
    }

    await utils.article.list.reset();
    await refetchFeedList();
    setWxsLink('');
    setIsModalOpen(false);
  };

  const exportOpml = () => {
    if (!items.length) {
      toast.error('还没有可导出的订阅源');
      return;
    }

    let opmlContent = `<?xml version="1.0" encoding="UTF-8"?>\n`;
    opmlContent += `<opml version="2.0">\n<head>\n<title>GZHReader 公众号后台订阅</title>\n</head>\n<body>\n`;
    items.forEach((sub) => {
      opmlContent += `  <outline text="${sub.mpName}" type="rss" xmlUrl="${serverOriginUrl}/feeds/${sub.id}.atom" htmlUrl="${serverOriginUrl}/feeds/${sub.id}.atom" />\n`;
    });
    opmlContent += `</body>\n</opml>`;

    const blob = new Blob([opmlContent], {
      type: 'text/xml;charset=utf-8;',
    });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'gzhreader-feeds.opml';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const refreshCurrentFeed = async () => {
    if (!currentMpInfo) {
      return;
    }

    const result = (await refreshMpArticles({
      mpId: currentMpInfo.id,
    })) as RefreshResult;
    await refetchFeedList();
    await utils.article.list.reset();
    toast.success('当前订阅已经刷新', {
      description: refreshSummaryText(result),
    });
  };

  const refreshAllFeeds = async () => {
    const result = (await refreshMpArticles({})) as RefreshResult;
    await refetchFeedList();
    await utils.article.list.reset();
    toast.success(
      result?.completed === false ? '本轮刷新已按预算暂停' : '订阅列表已经刷新',
      {
        description: refreshSummaryText(result),
      },
    );
  };

  const toggleHistorySync = async () => {
    if (!currentMpInfo) {
      return;
    }
    if (inProgressHistoryMp?.id === currentMpInfo.id) {
      await getHistoryArticles({ mpId: '' });
      toast.success('历史抓取已停止');
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

  return (
    <>
      <section className="rss-panel-hero rss-hero-grid">
        <div className="rss-hero-copy">
          <div className="rss-eyebrow">订阅编辑台</div>
          <h1 className="rss-title">把公众号更新收进一张更安静的编辑桌</h1>
          <div className="rss-rule" />
          <p className="rss-copy">
            这里负责订阅接入、文章刷新和聚合导出。新的刷新逻辑会优先保护账号健康，
            当预算到顶时自动分批，不再为了刷完一轮而把账号硬推到失效。
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
            <a
              className="rss-button is-secondary"
              href={`${serverOriginUrl}/feeds/all.atom`}
              target="_blank"
              rel="noreferrer"
            >
              打开聚合 Atom
            </a>
            <button
              type="button"
              className="rss-button is-secondary"
              onClick={exportOpml}
            >
              导出 OPML
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
            <strong>额度规则</strong>
            <br />
            每个账号每天最多 50 次，请求总预算达到 280 次后会自动暂停，剩余内容留到下一轮继续。
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
                {isFeedLoading ? '，目录更新中' : '，点击即可查看细节'}
              </p>
            </div>
          </div>
          <div className="rss-list">
            <button
              type="button"
              className={`rss-feed-item${currentMpId === '' ? ' is-active' : ''}`}
              onClick={() => {
                setCurrentMpId('');
                navigate('/feeds');
              }}
            >
              <div className="rss-feed-avatar rss-feed-avatar--placeholder">
                全
              </div>
              <div className="rss-stack">
                <div className="rss-feed-name">全部订阅</div>
                <div className="rss-feed-intro">
                  在这里统一刷新、打开聚合 Atom，或检查所有文章的收录情况。
                </div>
              </div>
            </button>

            {!items.length ? (
              <div className="rss-empty">
                还没有订阅源。粘贴一条公众号文章分享链接，就能把对应公众号接进后台。
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
                  <div className="rss-stack">
                    <div className="rss-feed-name">{item.mpName}</div>
                    <div className="rss-feed-intro">
                      {item.mpIntro || '已接入聚合源，点开可以继续刷新和查看文章列表'}
                    </div>
                  </div>
                </button>
              ))
            )}
          </div>
        </aside>

        <section className="rss-stack">
          <article className="rss-panel">
            <div className="rss-panel-header">
              <div>
                <h2 className="rss-panel-title">
                  {currentMpInfo?.mpName || '全部文章'}
                </h2>
                <p className="rss-panel-copy">
                  {currentMpInfo
                    ? currentMpInfo.mpIntro || '当前订阅的刷新、历史抓取与文章入口'
                    : '查看整体聚合结果，或从这里触发预算内的批量刷新'}
                </p>
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
                    <a
                      className="rss-button is-secondary"
                      href={`${serverOriginUrl}/feeds/${currentMpInfo.id}.atom`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开该订阅 Atom
                    </a>
                    <button
                      type="button"
                      className="rss-button is-danger"
                      disabled={isDeleteFeedLoading}
                      onClick={removeCurrentFeed}
                    >
                      删除当前订阅
                    </button>
                  </>
                ) : (
                  <>
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
                    <a
                      className="rss-button is-secondary"
                      href={`${serverOriginUrl}/feeds/all.atom`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开聚合 Atom
                    </a>
                  </>
                )}
              </div>
            </div>

            <div className="rss-note">
              {currentMpInfo
                ? `最近更新时间：${lastUpdatedLabel}`
                : '当总预算接近上限时，系统会自动暂停本轮刷新，并把剩余内容留到下一轮继续处理。'}
            </div>
          </article>

          <article className="rss-panel">
            <div className="rss-panel-header">
              <div>
                <h2 className="rss-panel-title">文章列表</h2>
                <p className="rss-panel-copy">
                  文章会按发布时间倒序显示，方便快速确认最新抓取结果。
                </p>
              </div>
            </div>
            <ArticleList />
          </article>
        </section>
      </div>

      {isModalOpen && (
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
                  粘贴公众号文章分享链接，后台会自动识别所属公众号并接入聚合源。可以一次粘贴多条，每行一条。
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
        </div>
      )}
    </>
  );
};

export default Feeds;
