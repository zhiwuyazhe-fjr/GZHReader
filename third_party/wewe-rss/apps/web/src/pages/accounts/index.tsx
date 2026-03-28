import { QRCodeSVG } from 'qrcode.react';
import { toast } from 'sonner';
import dayjs from 'dayjs';
import { useEffect, useMemo, useState } from 'react';
import { healthToneClassMap } from '@web/constants';
import { StatusDropdown } from '@web/components/StatusDropdown';
import { trpc } from '@web/utils/trpc';

const AccountPage = () => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [countdown, setCountdown] = useState(0);

  const { refetch, data, isFetching } = trpc.account.list.useQuery({});
  const utils = trpc.useUtils();

  const { mutateAsync: updateAccount } = trpc.account.edit.useMutation({});
  const { mutateAsync: deleteAccount } = trpc.account.delete.useMutation({});
  const { mutateAsync: addAccount } = trpc.account.add.useMutation({});
  const { mutateAsync: createLoginUrl, data: loginData } =
    trpc.platform.createLoginUrl.useMutation({
      onSuccess(result) {
        if (result.uuid) {
          setCountdown(60);
        }
      },
    });

  const { data: loginResult } = trpc.platform.getLoginResult.useQuery(
    {
      id: loginData?.uuid ?? '',
    },
    {
      enabled: !!loginData?.uuid,
      refetchIntervalInBackground: false,
      async onSuccess(result) {
        if (result.vid && result.token) {
          const name = result.username || `账号 ${result.vid}`;
          await addAccount({
            id: `${result.vid}`,
            name,
            token: result.token,
          });
          toast.success('账号已经加入后台', {
            description: `${name} 可以开始为订阅刷新提供额度`,
          });
          setIsModalOpen(false);
          setCountdown(0);
          await utils.account.list.reset();
          refetch();
          return;
        }

        if (result.message) {
          toast.error('扫码没有成功', {
            description: result.message,
          });
        }
      },
    },
  );

  useEffect(() => {
    if (!isModalOpen || countdown <= 0) {
      return;
    }
    const timer = window.setTimeout(() => {
      setCountdown((current) => current - 1);
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [countdown, isModalOpen]);

  const items = data?.items || [];
  const availableCount = useMemo(
    () => items.filter((item: any) => item.healthLabel === '可用').length,
    [items],
  );
  const cooldownCount = useMemo(
    () => items.filter((item: any) => item.healthLabel === '冷却中').length,
    [items],
  );
  const reloginCount = useMemo(
    () => items.filter((item: any) => item.healthLabel === '待重登').length,
    [items],
  );

  const openLoginModal = async () => {
    setIsModalOpen(true);
    await createLoginUrl();
  };

  const closeLoginModal = async () => {
    setIsModalOpen(false);
    setCountdown(0);
    await utils.platform.getLoginResult.cancel();
  };

  return (
    <>
      <section className="rss-panel-hero rss-hero-grid">
        <div className="rss-hero-copy">
          <div className="rss-eyebrow">账号池</div>
          <h1 className="rss-title">用更稳的账号池承接每天的刷新预算</h1>
          <div className="rss-rule" />
          <p className="rss-copy">
            这里不再只告诉你账号是不是存在，而是直接展示每个账号的真实健康状态、冷却原因和恢复入口。
            单次 401 不会再被立即误判成失效，整体调度也会优先保护可用额度。
          </p>
          <div className="rss-actions">
            <button
              type="button"
              className="rss-button is-primary"
              onClick={openLoginModal}
            >
              添加读书账号
            </button>
            <a className="rss-button is-secondary" href="/dash/feeds">
              前往订阅源
            </a>
          </div>
        </div>
        <aside className="rss-hero-side">
          <div className="rss-stat-item">
            <div className="rss-stat-label">可用账号</div>
            <div className="rss-stat-value">{availableCount}</div>
          </div>
          <div className="rss-stat-item">
            <div className="rss-stat-label">冷却中</div>
            <div className="rss-stat-copy">{cooldownCount} 个</div>
          </div>
          <div className="rss-stat-item">
            <div className="rss-stat-label">待重登</div>
            <div className="rss-stat-copy">{reloginCount} 个</div>
          </div>
          <div className="rss-note">
            <strong>使用提醒</strong>
            <br />
            登录微信读书时不要勾选“24 小时后自动退出”。新调度会先冷却、再判断是否需要重登。
          </div>
        </aside>
      </section>

      <section className="rss-panel">
        <div className="rss-panel-header">
          <div>
            <h2 className="rss-panel-title">账号列表</h2>
            <p className="rss-panel-copy">
              可用、冷却中、待重登和禁用都会直接显示在一行里，方便判断下一步是继续刷新还是重新扫码。
            </p>
          </div>
          <div className="rss-meta">
            <span>
              共 <strong>{items.length || 0}</strong> 个账号
            </span>
            <span>{isFetching ? '列表刷新中' : '状态已同步'}</span>
          </div>
        </div>

        <div className="rss-stack">
          {!items.length ? (
            <div className="rss-empty">
              还没有接入任何读书账号。点上面的“添加读书账号”，扫码后就能开始为订阅刷新提供额度。
            </div>
          ) : (
            items.map((item: any) => (
              <article key={item.id} className="rss-panel rss-account-card">
                <div className="rss-panel-header">
                  <div>
                    <h3 className="rss-panel-title">{item.name}</h3>
                    <p className="rss-panel-copy">账号 ID: {item.id}</p>
                  </div>
                  <span
                    className={`rss-chip ${
                      healthToneClassMap[
                        (item.healthTone as keyof typeof healthToneClassMap) ||
                          'warning'
                      ] || 'is-warning'
                    }`}
                  >
                    {item.healthLabel}
                  </span>
                </div>
                <div className="rss-grid-two">
                  <div className="rss-stack">
                    <div className="rss-note">{item.healthDetail}</div>
                    <div className="rss-meta">
                      <span>
                        今日请求 <strong>{item.dailyRequestCount || 0}</strong> / 50
                      </span>
                      <span>
                        最近成功
                        <strong>
                          {' '}
                          {item.lastSuccessAt
                            ? dayjs(item.lastSuccessAt).format(
                                'YYYY-MM-DD HH:mm:ss',
                              )
                            : '暂无'}
                        </strong>
                      </span>
                      <span>
                        更新时间
                        <strong>
                          {' '}
                          {dayjs(item.updatedAt).format('YYYY-MM-DD HH:mm')}
                        </strong>
                      </span>
                    </div>
                  </div>
                  <div className="rss-actions">
                    <StatusDropdown
                      value={item.status}
                      onChange={(value) => {
                        updateAccount({
                          id: item.id,
                          data: { status: value },
                        }).then(async () => {
                          toast.success('账号状态已更新');
                          await utils.account.list.reset();
                          refetch();
                        });
                      }}
                    />
                    <button
                      type="button"
                      className="rss-button is-danger"
                      onClick={() => {
                        deleteAccount(item.id).then(async () => {
                          toast.success('账号已经移除');
                          await utils.account.list.reset();
                          refetch();
                        });
                      }}
                    >
                      删除账号
                    </button>
                  </div>
                </div>
              </article>
            ))
          )}
        </div>
      </section>

      {isModalOpen && (
        <div className="rss-modal-backdrop" onClick={closeLoginModal}>
          <div
            className="rss-modal-card"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="rss-modal-head">
              <div className="rss-stack">
                <div className="rss-eyebrow">扫码恢复</div>
                <h2 className="rss-modal-title">添加或恢复读书账号</h2>
                <p className="rss-panel-copy">
                  扫码成功后，账号会立刻加入调度。新的调度策略会把短暂异常先放进冷却，而不是马上判成失效。
                </p>
              </div>
              <button
                type="button"
                className="rss-button rss-modal-close"
                onClick={closeLoginModal}
              >
                ×
              </button>
            </div>

            <div className="rss-qrcode-wrap">
              {loginData ? (
                <>
                  <div className="rss-qrcode-board">
                    {loginResult?.message && (
                      <div className="rss-qrcode-mask">{loginResult.message}</div>
                    )}
                    <QRCodeSVG size={170} value={loginData.scanUrl} />
                  </div>
                  <div className="rss-note">
                    微信扫码登录
                    {!loginResult?.message && countdown > 0
                      ? `，${countdown}s`
                      : ''}
                  </div>
                </>
              ) : (
                <div className="rss-empty">二维码准备中</div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default AccountPage;
