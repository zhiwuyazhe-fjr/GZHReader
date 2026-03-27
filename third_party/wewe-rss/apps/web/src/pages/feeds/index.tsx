import dayjs from 'dayjs';
import { MouseEvent, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { useNavigate, useParams } from 'react-router-dom';
import { serverOriginUrl } from '@web/utils/env';
import { trpc } from '@web/utils/trpc';
import ArticleList from './list';

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
  return `本次刷新处理了 ${refreshedCount} / ${totalCount} 个订阅${budgetRemaining}${reason}`;
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
  const { mutateAsync: updateMpInfo } = trpc.feed.edit.useMutation({});
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

  const currentMpInfo = useMemo(
    () => feedData?.items.find((item: any) => item.id === currentMpId),
    [currentMpId, feedData?.items],
  );

  const handleConfirm = async () => {
    const links = wxsLink
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean);

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
    refetchFeedList();
    setWxsLink('');
    setIsModalOpen(false);
  };

  const exportOpml = (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    if (!feedData?.items?.length) {
      toast.error('还没有可导出的订阅源');
      return;
    }

    let opmlContent = `<?xml version="1.0" encoding="UTF-8"?>\n`;
    opmlContent += `<opml version="2.0">\n<head>\n<title>GZHReader 公众号后台订阅</title>\n</head>\n<body>\n`;
    feedData.items.forEach((sub: any) => {
      opmlContent += `  <outline text="${sub.mpName}" type="rss" xmlUrl="${window.location.origin}/feeds/${sub.id}.atom" htmlUrl="${window.location.origin}/feeds/${sub.id}.atom" />\n`;
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
    } else {
      await getHistoryArticles({ mpId: currentMpInfo.id });
    }
    await refetchInProgressHistoryMp();
  };

  return (
    <>
      <section className="rss-panel-hero">
        <div className="rss-hero-copy">
          <div className="rss-eyebrow">订阅编辑台</div>
          <h1 className="rss-title">把公众号刷新收进一张更安静的编辑桌</h1>
          <div className="rss-rule" />
          <p className="rss-copy">
            这里负责订阅源、文章拉取和聚合输出。新的刷新逻辑会优先保护账号健康，预算到顶时自动分批，不再硬刷到账号失效
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
          </div>
        </div>
        <div className="rss-note">
          <strong>额度规则</strong>
          <br />
          每个账号每天最多 50 次，请求总预算达到 280 次后会自动停下，剩余内容留到下一轮继续
        </div>
      </section>

      <div className="rss-page-grid">
        <aside className="rss-sidebar">
          <div className="rss-panel-header">
            <div>
              <h2 className="rss-panel-title">订阅列表</h2>
              <p className="rss-panel-copy">
                共 {feedData?.items.length || 0} 个订阅
                {isFeedLoading ? '，列表更新中' : ''}
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
                  从这里统一更新、导出 OPML，或查看所有文章列表
                </div>
              </div>
            </button>

            {(feedData?.items || []).length === 0 ? (
              <div className="rss-empty">
                还没有订阅源。添加一条公众号文章分享链接，就能把对应公众号接进后台
              </div>
            ) : (
              (feedData?.items || []).map((item: any) => (
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
                      {item.mpIntro || '已接入聚合源，点击查看文章与刷新状态'}
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
                    ? currentMpInfo.mpIntro || '当前订阅的刷新、历史拉取与导出入口'
                    : '查看全量聚合结果，或从这里触发一轮预算内刷新'}
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
                        disabled={
                          isHistoryLoading ||
                          isRefreshLoading ||
                          Boolean(
                            inProgressHistoryMp?.id &&
                              inProgressHistoryMp?.id !== currentMpInfo.id,
                          )
                        }
                        onClick={toggleHistorySync}
                      >
                        {inProgressHistoryMp?.id === currentMpInfo.id
                          ? '停止历史拉取'
                          : '拉取历史文章'}
                      </button>
                    )}
                    <button
                      type="button"
                      className={`rss-button ${
                        currentMpInfo.status === 1
                          ? 'is-secondary'
                          : 'is-success'
                      }`}
                      onClick={async () => {
                        await updateMpInfo({
                          id: currentMpInfo.id,
                          data: {
                            status: currentMpInfo.status === 1 ? 0 : 1,
                          },
                        });
                        await refetchFeedList();
                      }}
                    >
                      {currentMpInfo.status === 1 ? '暂停自动更新' : '恢复自动更新'}
                    </button>
                    <button
                      type="button"
                      className="rss-button is-danger"
                      disabled={isDeleteFeedLoading}
                      onClick={async () => {
                        if (!window.confirm('确定要删除这个订阅吗？')) {
                          return;
                        }
                        await deleteFeed(currentMpInfo.id);
                        navigate('/feeds');
                        await refetchFeedList();
                      }}
                    >
                      删除订阅
                    </button>
                    <a
                      className="rss-button is-secondary"
                      href={`${serverOriginUrl}/feeds/${currentMpInfo.id}.atom`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开该订阅 Atom
                    </a>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      className="rss-button is-secondary"
                      onClick={exportOpml}
                    >
                      导出 OPML
                    </button>
                    <a
                      className="rss-button is-secondary"
                      href={`${serverOriginUrl}/feeds/all.atom`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开全部 Atom
                    </a>
                  </>
                )}
              </div>
            </div>

            <div className="rss-meta">
              {currentMpInfo ? (
                <>
                  <span>
                    最后同步
                    <strong>
                      {' '}
                      {currentMpInfo.syncTime
                        ? dayjs(currentMpInfo.syncTime * 1000).format(
                            'YYYY-MM-DD HH:mm:ss',
                          )
                        : '暂无'}
                    </strong>
                  </span>
                  <span>
                    自动更新
                    <strong>
                      {' '}
                      {currentMpInfo.status === 1 ? '已启用' : '已暂停'}
                    </strong>
                  </span>
                  {inProgressHistoryMp?.id === currentMpInfo.id && (
                    <span>
                      历史拉取
                      <strong> 第 {inProgressHistoryMp.page} 页进行中</strong>
                    </span>
                  )}
                </>
              ) : (
                <>
                  <span>
                    聚合输出
                    <strong> {serverOriginUrl}/feeds/all.atom</strong>
                  </span>
                  <span>
                    刷新策略
                    <strong> 预算内分批</strong>
                  </span>
                </>
              )}
            </div>
          </article>

          <article className="rss-panel">
            <ArticleList />
          </article>
        </section>
      </div>

      {isModalOpen && (
        <div
          className="rss-modal-backdrop"
          onClick={() => setIsModalOpen(false)}
        >
          <div
            className="rss-modal-card"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="rss-modal-head">
              <div className="rss-stack">
                <div className="rss-eyebrow">接入订阅</div>
                <h2 className="rss-modal-title">添加公众号订阅源</h2>
                <p className="rss-panel-copy">
                  把公众号文章分享链接贴进来，一行一条。后台会自动识别公众号信息，并完成首轮刷新
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
                placeholder="每行一条 https://mp.weixin.qq.com/s/..."
                autoFocus
              />
            </label>
            <div className="rss-actions">
              <button
                type="button"
                className="rss-button is-secondary"
                onClick={() => setIsModalOpen(false)}
              >
                取消
              </button>
              <button
                type="button"
                className="rss-button is-primary"
                disabled={
                  !wxsLink
                    .split('\n')
                    .some((line) =>
                      line.trim().startsWith('https://mp.weixin.qq.com/s/'),
                    ) ||
                  isAddFeedLoading ||
                  isGetMpInfoLoading ||
                  isRefreshLoading
                }
                onClick={handleConfirm}
              >
                {isAddFeedLoading || isGetMpInfoLoading || isRefreshLoading
                  ? '处理中'
                  : '确认添加'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default Feeds;
