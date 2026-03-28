import { useEffect, useMemo, useState } from 'react';
import dayjs from 'dayjs';
import { useParams } from 'react-router-dom';
import { trpc } from '@web/utils/trpc';

type PageToken = number | 'gap-left' | 'gap-right';

const buildPageTokens = (currentPage: number, pageCount: number): PageToken[] => {
  if (pageCount <= 7) {
    return Array.from({ length: pageCount }, (_, index) => index + 1);
  }

  const tokens: PageToken[] = [1];
  const windowStart = Math.max(2, currentPage - 1);
  const windowEnd = Math.min(pageCount - 1, currentPage + 1);

  if (windowStart > 2) {
    tokens.push('gap-left');
  }

  for (let page = windowStart; page <= windowEnd; page += 1) {
    tokens.push(page);
  }

  if (windowEnd < pageCount - 1) {
    tokens.push('gap-right');
  }

  tokens.push(pageCount);
  return tokens;
};

const ArticleList = () => {
  const { id } = useParams();
  const mpId = id || '';
  const [page, setPage] = useState(1);
  const pageSize = 12;

  useEffect(() => {
    setPage(1);
  }, [mpId]);

  const { data, isLoading, isFetching } = trpc.article.list.useQuery(
    {
      page,
      pageSize,
      mpId,
    },
    {
      keepPreviousData: true,
      refetchOnWindowFocus: true,
    },
  );

  const items = data?.items || [];
  const currentPage = data?.page || page;
  const pageCount = data?.pageCount || 1;
  const pageTokens = useMemo(
    () => buildPageTokens(currentPage, pageCount),
    [currentPage, pageCount],
  );

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

      {pageCount > 1 && (
        <div className="rss-pagination">
          <button
            type="button"
            className="rss-button is-secondary"
            disabled={currentPage <= 1 || isFetching}
            onClick={() => setPage((value) => Math.max(value - 1, 1))}
          >
            上一页
          </button>
          <div className="rss-pagination-pages">
            {pageTokens.map((token, index) =>
              typeof token === 'number' ? (
                <button
                  key={token}
                  type="button"
                  className={`rss-page-pill${
                    token === currentPage ? ' is-active' : ''
                  }`}
                  disabled={isFetching}
                  onClick={() => setPage(token)}
                >
                  {token}
                </button>
              ) : (
                <span key={`${token}-${index}`} className="rss-page-gap" aria-hidden="true">
                  …
                </span>
              ),
            )}
          </div>
          <button
            type="button"
            className="rss-button is-secondary"
            disabled={currentPage >= pageCount || isFetching}
            onClick={() => setPage((value) => Math.min(value + 1, pageCount))}
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );
};

export default ArticleList;
