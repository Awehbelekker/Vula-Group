/**
 * ListRow — one line of a list (2026-10-17, UX redesign Phase C): an icon disc (or initials),
 * a title, a detail line, and a trailing time or status. At least 56px high. A button when it
 * has onClick, a link when it has href, otherwise plain — so lists stay keyboard reachable
 * (the old clickable <div> rows weren't).
 */
export function ListRow({ Icon, initials, title, detail, trailing, onClick, href, className, ...rest }) {
  const Tag = href ? "a" : onClick ? "button" : "div";
  return (
    <Tag className={`vui-row${className ? ` ${className}` : ""}`} href={href} onClick={onClick}
      type={Tag === "button" ? "button" : undefined} {...rest}>
      {(Icon || initials) && (
        <span className="vui-disc" aria-hidden="true">
          {Icon ? <Icon size={18} strokeWidth={1.75} /> : initials}
        </span>)}
      <span className="vui-row-main">
        <span className="vui-row-title" style={{ display: "block" }}>{title}</span>
        {detail && <span className="vui-row-detail" style={{ display: "block" }}>{detail}</span>}
      </span>
      {trailing != null && <span className="vui-row-trail">{trailing}</span>}
    </Tag>
  );
}
