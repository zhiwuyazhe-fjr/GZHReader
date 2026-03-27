import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { enabledAuthCode } from '@web/utils/env';
import { setAuthCode } from '@web/utils/auth';

const LoginPage = () => {
  const [codeValue, setCodeValue] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    if (!enabledAuthCode) {
      navigate('/', { replace: true });
    }
  }, [navigate]);

  if (!enabledAuthCode) {
    return null;
  }

  return (
    <div className="rss-auth-shell">
      <div className="rss-auth-card">
        <div className="rss-eyebrow">后台入口</div>
        <h1 className="rss-auth-title">输入访问码</h1>
        <p className="rss-auth-copy">
          当前部署启用了访问码保护。输入正确的 auth code 后，才能继续进入公众号后台
        </p>
        <label className="rss-field">
          <span className="rss-field-label">Auth Code</span>
          <input
            className="rss-input"
            value={codeValue}
            onChange={(event) => setCodeValue(event.target.value)}
            placeholder="输入 auth code"
          />
        </label>
        <button
          type="button"
          className="rss-button is-primary"
          onClick={() => {
            setAuthCode(codeValue);
            navigate('/');
          }}
        >
          进入后台
        </button>
      </div>
    </div>
  );
};

export default LoginPage;
