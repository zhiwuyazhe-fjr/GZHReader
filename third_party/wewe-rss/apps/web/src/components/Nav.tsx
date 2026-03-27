import { NavLink } from 'react-router-dom';
import { ThemeSwitcher } from './ThemeSwitcher';
import { appVersion, serverOriginUrl } from '@web/utils/env';

const navItems = [
  {
    href: '/feeds',
    name: '订阅源',
  },
  {
    href: '/accounts',
    name: '账号管理',
  },
] as const;

const Nav = () => {
  return (
    <header className="rss-topbar">
      <div className="rss-brand">
        <img
          className="rss-brand-mark"
          src={`${serverOriginUrl}/favicon.ico`}
          alt="公众号后台"
        />
        <div className="rss-brand-copy">
          <div className="rss-brand-kicker">Bundled Service</div>
          <div className="rss-brand-title-row">
            <div className="rss-brand-title">公众号后台</div>
            <span className="rss-version-badge">v{appVersion}</span>
          </div>
          <div className="rss-brand-subtitle">
            扫码登录、维护订阅和刷新聚合源都收在这里
          </div>
        </div>
      </div>
      <div className="rss-topbar-actions">
        <nav className="rss-topnav" aria-label="后台导航">
          {navItems.map((item) => (
            <NavLink
              key={item.href}
              to={item.href}
              className={({ isActive }) =>
                `rss-nav-link${isActive ? ' is-active' : ''}`
              }
            >
              {item.name}
            </NavLink>
          ))}
        </nav>
        <ThemeSwitcher />
      </div>
    </header>
  );
};

export default Nav;
