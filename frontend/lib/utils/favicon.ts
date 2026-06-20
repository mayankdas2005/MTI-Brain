/**
 * Paints a small colored dot on the favicon to signal unread state.
 * Pairs with the tab-title badge - visible across browser tab strips
 * and most OS taskbars even when the tab is muted/scrolled.
 */

const DEFAULT_FAVICON = `${process.env.NEXT_PUBLIC_BASE_PATH || ''}/favicon.ico`;
let originalHref: string | null = null;
let lastAppliedKey = '';

function findIconLink(): HTMLLinkElement | null {
  if (typeof document === 'undefined') return null;
  return (
    document.querySelector<HTMLLinkElement>('link[rel~="icon"]') ??
    document.querySelector<HTMLLinkElement>('link[rel="shortcut icon"]')
  );
}

function ensureLink(): HTMLLinkElement | null {
  let link = findIconLink();
  if (!link) {
    if (typeof document === 'undefined') return null;
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  if (originalHref === null) originalHref = link.href || DEFAULT_FAVICON;
  return link;
}

function paintBadge(baseHref: string, color: string, onReady: (dataUrl: string) => void) {
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = () => {
    const size = 32;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.drawImage(img, 0, 0, size, size);
    const r = 7;
    const cx = size - r - 1;
    const cy = size - r - 1;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 2;
    ctx.stroke();
    onReady(canvas.toDataURL('image/png'));
  };
  // Cross-origin or missing favicons fail silently - we just keep the
  // original icon. No alarm.
  img.onerror = () => {};
  img.src = baseHref;
}

/** Apply a colored dot. Idempotent if called repeatedly with the same color. */
export function setFaviconDot(color = '#1B76B8') {
  const link = ensureLink();
  if (!link || !originalHref) return;
  const key = `dot:${color}`;
  if (lastAppliedKey === key) return;
  lastAppliedKey = key;
  paintBadge(originalHref, color, (dataUrl) => {
    if (lastAppliedKey === key) link.href = dataUrl;
  });
}

/** Restore the original favicon. */
export function clearFaviconDot() {
  const link = ensureLink();
  if (!link || !originalHref) return;
  if (lastAppliedKey === '') return;
  lastAppliedKey = '';
  link.href = originalHref;
}
