import { useMemo } from 'react';
import dayjs from 'dayjs';
import { useParams } from 'react-router-dom';
import { trpc } from '@web/utils/trpc';

const ArticleList = () => {
  const { id } = useParams();
  const mpId = id || '';
  const { data, fetchNextPage, isLoading, hasNextPage, isFetchingNextPage } =
    trpc.article.list.useInfiniteQuery(
      {
        limit: 20,
        mpId,
      },
      {
        getNextPageParam: (lastPage) => lastPage.nextCursor,
      },
    );

  const items = useMemo(() => {
    if (!data) {
      return [] as any[];
    }
    return data.pages.flatMap((page) => page.items);
  }, [data]);

  return (
    <div className="rss-table-shell">
      <div className="rss-table">
        <div className="rss-table-row rss-table-head">
          <div>文章标题</div>
          <div>发布时间</div>
        </div>
        {isLoading ? (
          <div className="rss-empty">文章列表加载中</div>
        ) : items.length === 0 ? (
          <div className="rss-empty">这里还没有文章，先刷新一次订阅源试试看</div>
        ) : (
          items.map((item: any) => (
            <div className="rss-table-row" key={item.id}>
              <div>
                <a
                  className="rss-article-link"
                  target="_blank"
                  rel="noreferrer"
                  href={`https://mp.weixin.qq.com/s/${item.id}`}
                >
                  {item.title}
                </a>
              </div>
              <div className="rss-note">
                {dayjs(item.publishTime * 1e3).format('YYYY-MM-DD HH:mm:ss')}
              </div>
            </div>
          ))
        )}
      </div>

      {hasNextPage && (
        <div className="rss-actions">
          <button
            type="button"
            className="rss-button is-secondary"
            disabled={isFetchingNextPage}
            onClick={() => fetchNextPage()}
          >
            {isFetchingNextPage ? '继续加载中' : '加载更多'}
          </button>
        </div>
      )}
    </div>
  );
};

export default ArticleList;
