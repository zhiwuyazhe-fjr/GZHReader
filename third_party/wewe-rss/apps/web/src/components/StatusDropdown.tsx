import { statusMap } from '@web/constants';

export function StatusDropdown({
  value = 1,
  onChange,
}: {
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="rss-inline-field">
      <span className="rss-inline-label">状态</span>
      <select
        className="rss-select rss-inline-select"
        aria-label="设置账号状态"
        value={String(value)}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {Object.entries(statusMap).map(([key, item]) => (
          <option key={key} value={key} disabled={key === '0'}>
            {item.label}
          </option>
        ))}
      </select>
    </label>
  );
}
