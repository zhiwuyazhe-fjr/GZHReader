import { Navigate } from 'react-router-dom';
import { enabledAuthCode } from '@web/utils/env';
import { clearAuthCode, getAuthCode, setAuthCode } from '@web/utils/auth';

export default function LoginPage() {
  if (!enabledAuthCode) {
    clearAuthCode();
    return <Navigate to="/" replace />;
  }

  return (
    <div className="rss-auth-shell">
      <div className="rss-auth-card">
        <div className="rss-eyebrow">兼容入口</div>
        <h1 className="rss-auth-title">输入访问码后继续进入公众号后台</h1>
        <p className="rss-auth-copy">
          当前部署启用了访问码保护。输入正确的访问码后，才能继续使用订阅与账号管理。
        </p>
        <label className="rss-field">
          <span className="rss-field-label">访问码</span>
          <input
            className="rss-input"
            type="password"
            defaultValue={getAuthCode() ?? ''}
            onChange={(event) => setAuthCode(event.target.value)}
          />
        </label>
      </div>
    </div>
  );
}
