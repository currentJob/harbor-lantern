/* 진행률 링 + 'n/m 완료' (설계서 §6.15 · AC-030 — 원본 자산 계승)
 *
 * 퍼센트는 **서버가 계산한 값**(progress.percent)을 그대로 그린다.
 * 프론트에서 다시 round 하면 JS Math.round(반올림)와 파이썬 round(은행가 반올림)가
 * 갈리는 자리를 하나 더 만드는 셈이다(설계서 §12 F4). 계산은 한 곳에서만 한다.
 *
 * stroke-dasharray 150.8 = 2πr (r=24). 원본 값 그대로.
 */

const RING_CIRCUMFERENCE = 150.8;

export function renderProgress(progress) {
  const ringText = document.getElementById('ringtxt');
  const ringFg = document.getElementById('ringfg');
  const doneCount = document.getElementById('donecount');
  if (!ringText || !ringFg || !doneCount) return;

  const done = (progress && progress.done) || 0;
  const total = (progress && progress.total) || 0;
  const percent = progress && typeof progress.percent === 'number'
    ? progress.percent
    : (total ? Math.round((done / total) * 100) : 0); // 서버 값이 없을 때만 쓰는 예비 계산

  ringText.textContent = `${percent}%`;
  ringFg.style.strokeDashoffset = String(RING_CIRCUMFERENCE * (1 - percent / 100));
  doneCount.textContent = `${done}/${total} 완료`;
}
