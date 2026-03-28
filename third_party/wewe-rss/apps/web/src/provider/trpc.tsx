import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { httpBatchLink, loggerLink } from '@trpc/client';
import { useNavigate } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { clearAuthCode, getAuthCode, setAuthCode } from '../utils/auth';
import { enabledAuthCode, serverOriginUrl } from '../utils/env';
import { isTRPCClientError, trpc } from '../utils/trpc';

const reconnectMessage = '删除账号后，重新扫码添加';

export const TrpcProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const navigate = useNavigate();

  useEffect(() => {
    if (!enabledAuthCode) {
      clearAuthCode();
    }
  }, []);

  const handleNoAuth = () => {
    if (!enabledAuthCode) {
      clearAuthCode();
      return;
    }

    setAuthCode('');
    navigate('/login');
  };

  const isHandledAccountStateError = (message: string) =>
    message.includes(reconnectMessage) ||
    message.includes('没有可用账号') ||
    message.includes('当前账号需要重新登录');

  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            refetchOnWindowFocus: false,
            refetchOnReconnect: true,
            refetchIntervalInBackground: false,
            retryDelay: (retryCount) => Math.min(retryCount * 1000, 60 * 1000),
            retry(failureCount, error) {
              if (isTRPCClientError(error) && error.data?.httpStatus === 401) {
                return false;
              }
              return failureCount < 3;
            },
            onError(error) {
              if (!isTRPCClientError(error)) {
                return;
              }

              if (error.data?.httpStatus === 401) {
                if (isHandledAccountStateError(error.message)) {
                  return;
                }
                if (enabledAuthCode) {
                  toast.error('后台访问码已失效', {
                    description: error.message,
                  });
                }
                handleNoAuth();
                return;
              }

              if (!isHandledAccountStateError(error.message)) {
                toast.error('请求没有成功', {
                  description: error.message,
                });
              }
            },
          },
          mutations: {
            onError(error) {
              if (!isTRPCClientError(error)) {
                return;
              }

              if (error.data?.httpStatus === 401) {
                if (isHandledAccountStateError(error.message)) {
                  return;
                }
                if (enabledAuthCode) {
                  toast.error('后台访问码已失效', {
                    description: error.message,
                  });
                }
                handleNoAuth();
                return;
              }

              if (!isHandledAccountStateError(error.message)) {
                toast.error('请求没有成功', {
                  description: error.message,
                });
              }
            },
          },
        },
      }),
  );

  const [trpcClient] = useState(() =>
    trpc.createClient({
      links: [
        loggerLink({
          enabled: () => true,
        }),
        httpBatchLink({
          url: `${serverOriginUrl}/trpc`,
          async headers() {
            if (!enabledAuthCode) {
              clearAuthCode();
              return {};
            }

            const token = getAuthCode();
            if (!token) {
              handleNoAuth();
              return {};
            }

            return {
              Authorization: `${token}`,
            };
          },
        }),
      ],
    }),
  );

  return (
    <trpc.Provider client={trpcClient} queryClient={queryClient}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </trpc.Provider>
  );
};
