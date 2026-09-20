(function () {
  const panel = document.querySelector("#card-game");
  if (!panel) return;

  const token = panel.dataset.token;
  const csrf = panel.dataset.csrf;
  const root = document.querySelector("#card-game-root");

  async function fetchState() {
    const response = await fetch(`/api/p/${token}/state`, { credentials: "same-origin" });
    if (!response.ok) return;
    const state = await response.json();
    render(state);
  }

  function render(state) {
    if (!state.deployed) {
      root.className = "activity-empty";
      root.textContent = "Waiting for your facilitator to deploy the activity.";
      return;
    }
    root.className = "card-game-layout";
    const playable = state.assignments.filter((card) => card.current_owner_participant_id === card.participant_id && card.status === "assigned");
    const given = state.assignments.filter((card) => card.status === "given");
    root.innerHTML = `
      <div>
        <h3>Your cards</h3>
        <div class="card-grid">
          ${playable.length ? playable.map((card) => cardTemplate(card, state.participants)).join("") : `<p class="muted">You have acted on every starting card.</p>`}
        </div>
        ${given.length ? `<h3>Given away</h3><div class="kept-received">${given.map((card) => miniCard(card, "Given to another participant")).join("")}</div>` : ""}
      </div>
      <aside>
        <h3>Cards Kept and Received</h3>
        <div class="kept-received">
          ${state.kept.length ? state.kept.map((card) => miniCard(card, "Kept by you")).join("") : `<p class="muted">No cards kept yet.</p>`}
          ${state.received.length ? state.received.map((card) => miniCard(card, `Received from ${escapeHtml(card.giver_name)}`)).join("") : `<p class="muted">No cards received yet.</p>`}
        </div>
      </aside>
    `;
  }

  function cardTemplate(card, participants) {
    const options = participants.map((p) => `<option value="${p.id}">${escapeHtml(p.full_name)}</option>`).join("");
    return `
      <article class="preference-card" data-colour="${escapeHtml(card.colour)}">
        <div>
          <div class="card-kicker">${escapeHtml(card.colour)}</div>
          <p>${escapeHtml(card.content)}</p>
        </div>
        <div class="card-actions">
          <button class="button primary" data-action="keep" data-card="${card.id}" type="button">Keep for me</button>
          <div class="give-row">
            <select aria-label="Choose recipient" data-recipient="${card.id}">
              <option value="">Give to...</option>
              ${options}
            </select>
            <button class="button" data-action="give" data-card="${card.id}" type="button">Send</button>
          </div>
        </div>
      </article>
    `;
  }

  function miniCard(card, label) {
    return `
      <div class="mini-card" data-colour="${escapeHtml(card.colour)}">
        <div class="card-kicker">${escapeHtml(card.colour)} - ${label}</div>
        <p>${escapeHtml(card.content)}</p>
      </div>
    `;
  }

  async function act(cardId, action, recipientId) {
    const options = {
      method: "POST",
      headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(recipientId ? { recipient_id: recipientId } : {}),
    };
    const response = await fetch(`/api/p/${token}/cards/${cardId}/${action}`, options);
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      alert(payload.error || "That card could not be updated. Please refresh and try again.");
      return;
    }
    render(payload.state);
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  root.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const cardId = button.dataset.card;
    const action = button.dataset.action;
    if (action === "keep") {
      act(cardId, "keep");
      return;
    }
    const select = root.querySelector(`select[data-recipient="${cardId}"]`);
    const recipientId = select && select.value;
    if (!recipientId) {
      alert("Choose who should receive this card.");
      return;
    }
    act(cardId, "give", recipientId);
  });

  fetchState();
  window.setInterval(fetchState, 4000);
})();
