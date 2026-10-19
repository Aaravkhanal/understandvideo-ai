export function formatTime(value = 0) {
  const seconds = Math.max(0, Math.floor(Number.isFinite(value) ? value : 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  if (hours > 0) {
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
  }
  return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
}

export function timestampToSeconds(minutes, seconds) {
  return Math.max(0, Number(minutes) * 60 + Number(seconds));
}

export function timestampPattern() {
  return /(\[\[\d{2}:\d{2}\]\])/g;
}
