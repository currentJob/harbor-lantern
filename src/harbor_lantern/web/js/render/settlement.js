/* 정산 결과 — 누가 누구에게 얼마 (설계서 §6.6 · REQ-008 · AC-014~AC-016)
 *
 * 계산은 전부 서버가 한다(최소 송금 그리디 · 잔액 합계 0). 여기서는 그 결과를
 * 사람이 읽을 수 있는 문장으로 바꿀 뿐이다 — 프론트가 다시 계산하면 두 개의 진실이 생긴다.
 */

import { escapeHtml, formatHkd } from '../format.js';

export function renderSettlement(container, settlement) {
  if (!settlement) {
    container.innerHTML = '<h2>정산</h2><div class="empty">경비가 기록되면 정산 결과가 여기 표시됩니다.</div>';
    return;
  }
  const balances = settlement.balances || [];
  const transfers = settlement.transfers || [];

  const balanceRows = balances.map((b) => {
    const sign = b.balance_minor > 0 ? 'plus' : (b.balance_minor < 0 ? 'minus' : '');
    const label = b.balance_minor > 0 ? '받을 돈' : (b.balance_minor < 0 ? '낼 돈' : '정산 완료');
    return `<div class="rowitem balance ${sign}">`
      + `<div><div class="who">${escapeHtml(b.display_name)}</div>`
      + `<div class="memo">낸 돈 ${escapeHtml(formatHkd(b.paid_minor))} · `
      + `부담 ${escapeHtml(formatHkd(b.owed_minor))}</div></div>`
      + `<div class="amt">${escapeHtml(formatHkd(Math.abs(b.balance_minor)))}<small>${escapeHtml(label)}</small></div>`
      + '</div>';
  }).join('');

  const nameOf = (id) => {
    const hit = balances.find((b) => b.participant_id === id);
    return hit ? hit.display_name : id;
  };

  const transferRows = transfers.length
    ? transfers.map((t) =>
      '<div class="rowitem transfer">'
      + `<div><div class="who">${escapeHtml(nameOf(t.from_participant_id))} → `
      + `${escapeHtml(nameOf(t.to_participant_id))}</div>`
      + '<div class="memo">이 한 번으로 정리됩니다</div></div>'
      + `<div class="amt">${escapeHtml(formatHkd(t.amount_minor))}</div></div>`).join('')
    : '<div class="empty">주고받을 돈이 없습니다.</div>';

  container.innerHTML = '<h2>정산 <span>최소 송금</span></h2>'
    + transferRows
    + (balanceRows ? `<h2 style="margin-top:14px">잔액 <span>낸 돈 − 부담</span></h2>${balanceRows}` : '');
}
