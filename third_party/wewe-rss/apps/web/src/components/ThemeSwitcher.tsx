import { useTheme } from 'next-themes';

const options = [
  { value: 'system', label: '跟随系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
] as const;

export function ThemeSwitcher() {
  const { setTheme, theme } = useTheme();
  const currentTheme = theme || 'system';

  return (
    <div className="rss-theme-switch" role="group" aria-label="切换主题">
      {options.map((option) => {
        const active = currentTheme === option.value;
        return (
          <button
            key={option.value}
            type="button"
            className={`rss-theme-option${active ? ' is-active' : ''}`}
            aria-pressed={active}
            onClick={() => setTheme(option.value)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
