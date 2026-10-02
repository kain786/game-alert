'use strict';
(() => {
  const button = document.getElementById('refresh-button');
  const feedback = document.getElementById('refresh-feedback');
  let pollTimer;
  async function poll(started, baseline) {
    try {
      const response = await fetch('/api/refresh/status', {cache: 'no-store'});
      if (!response.ok) throw new Error('status');
      const status = await response.json();
      if (status.started_at && status.started_at !== baseline && ['success', 'partial', 'failed'].includes(status.state)) {
        if (status.state === 'success') { window.location.reload(); return; }
        feedback.textContent = '일부 일정을 갱신하지 못했습니다. 마지막 정상 자료를 표시합니다. 페이지를 다시 열면 상세 상태를 확인할 수 있습니다.';
        button.disabled = false;
        return;
      }
      if (Date.now() - started > 210000) {
        feedback.textContent = '갱신 완료를 확인하지 못했습니다. 기존 일정은 계속 사용할 수 있습니다.';
        button.disabled = false;
        return;
      }
      feedback.textContent = status.state === 'running' ? '일정을 갱신하고 있습니다…' : '갱신 요청을 기다리고 있습니다…';
      pollTimer = window.setTimeout(() => poll(started, baseline), 3000);
    } catch (_) {
      feedback.textContent = '갱신 상태를 확인하지 못했습니다. 연결이나 로그인 상태를 확인해 주세요.';
      button.disabled = false;
    }
  }
  button.addEventListener('click', async () => {
    button.disabled = true;
    clearTimeout(pollTimer);
    try {
      const before = await fetch('/api/refresh/status', {cache: 'no-store'});
      if (!before.ok) throw new Error('status');
      const baseline = (await before.json()).started_at;
      const response = await fetch('/api/refresh', {method: 'POST', headers: {'X-Gevent-Request': 'refresh'}});
      const result = await response.json();
      feedback.textContent = result.message || result.error || '갱신 요청을 확인하지 못했습니다.';
      if (response.ok) await poll(Date.now(), result.state === 'running' ? undefined : baseline);
      else button.disabled = false;
    } catch (_) {
      feedback.textContent = '갱신 요청에 실패했습니다. 연결이나 로그인 상태를 확인해 주세요.';
      button.disabled = false;
    }
  });
})();
