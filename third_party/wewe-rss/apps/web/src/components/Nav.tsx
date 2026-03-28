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
    name: '账号池',
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
          <div className="rss-brand-kicker">Built Into GZHReader</div>
          <div className="rss-brand-title-row">
            <div className="rss-brand-title">公众号后台</div>
            <span className="rss-version-badge">v{appVersion}</span>
          </div>
          <div className="rss-brand-subtitle">
            在这里维护账号、订阅与聚合源，保持每天的阅读整理安静而稳定
          </div>
        </div>
      </div>
      <div className="rss-topbar-actions">
        <nav className="rss-topnav" aria-label="公众号后台导航">
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
