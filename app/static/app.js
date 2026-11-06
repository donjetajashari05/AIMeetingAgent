'use strict';

/* ================= Helpers ================= */

const ICONS = {
  wave: '<path d="M4 10v4M8 6v12M12 9v6M16 4v16M20 10v4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  grid: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  check: '<path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  tick: '<path d="M5 12l5 5 9-10"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/>',
  retry: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6.5 6.5 0 0 1 3.5 5.5"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>',
  file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  jira: '<path d="M12 3l9 9-9 9-9-9z"/><path d="M12 8l4 4-4 4-4-4z"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  book: '<path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5z"/><path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19v-3M9 7h6"/>',
};

function icon(name, size = 18, strokeWidth = 1.9) {
  const span = document.createElement('span');
  span.setAttribute('aria-hidden', 'true');
  span.style.display = 'inline-flex';
  // Only constant strings from ICONS are inserted as markup
  span.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ''}</svg>`;
  return span;
}

/** el('div', {class: 'x', onclick: fn, text: '...'}, child1, child2) — all text via textContent. */
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k in node && typeof v !== 'string') node[k] = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/** Drop null/false before append()/replaceChildren(), which would print them as text. */
const nn = (...xs) => xs.flat(Infinity).filter((x) => x !== null && x !== undefined && x !== false);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    showLogin();  // session expired or not logged in
    throw new Error('Please sign in again.');
  }
  if (!res.ok) throw new Error((data && data.detail) || `Error ${res.status}`);
  return data;
}

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove('show'), 3200);
}

/* ================= Formatting ================= */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_FULL = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
// Readable on white (text ≥ 4.5:1) and as avatar backgrounds under white text
const SPEAKER_COLORS = ['#683790', '#17794E', '#946200', '#B8336A', '#1F7A99', '#C2410C'];

const parseDate = (iso) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const fmtLong = (iso) => { const d = parseDate(iso); return `${DAYS[d.getDay()]}, ${MONTHS_FULL[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`; };
const fmtShort = (iso) => { const d = parseDate(iso); return `${MONTHS[d.getMonth()]} ${d.getDate()}`; };
const daysBetween = (a, b) => Math.round((parseDate(b) - parseDate(a)) / 86400000);

function fmtClock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(2, '0'), ss = String(s).padStart(2, '0');
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function fmtDuration(sec) {
  if (!sec) return null;
  const m = Math.round(sec / 60);
  if (m < 1) return `${Math.round(sec)} sec`;
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min` : `${m} min`;
}

function duePill(task) {
  if (task.status === 'done') return el('span', { class: 'pill done', text: 'Done' });
  if (!task.due_date) return el('span', { class: 'pill', text: 'No due date' });
  const diff = daysBetween(state.today, task.due_date);
  if (diff < 0) return el('span', { class: 'pill late', text: `${-diff} day${-diff === 1 ? '' : 's'} late` });
  if (diff === 0) return el('span', { class: 'pill today', text: 'Today' });
  if (diff === 1) return el('span', { class: 'pill today', text: 'Tomorrow' });
  const d = parseDate(task.due_date);
  const label = diff < 7 ? `${DAYS[d.getDay()].slice(0, 3)}, ${fmtShort(task.due_date)}` : fmtShort(task.due_date);
  return el('span', { class: 'pill soon', text: label, title: task.due_date });
}

const STATUS = {
  queued: ['Queued', 'work'],
  transcribing: ['Transcribing', 'work'],
  diarizing: ['Speakers', 'work'],
  extracting: ['Analyzing', 'work'],
  done: ['Processed', 'done'],
  needs_key: ['Not analyzed', 'warn'],
  error: ['Error', 'error'],
};
const isBusy = (m) => ['queued', 'transcribing', 'diarizing', 'extracting'].includes(m.status);

function statusChip(m) {
  const [label, cls] = STATUS[m.status] || [m.status, 'work'];
  const text = m.status === 'transcribing' ? `${label} ${Math.round((m.progress || 0) * 100)}%` : label;
  return el('span', { class: `status ${cls}`, text });
}

/* ================= State & router ================= */

const state = { me: null, status: null, org: { employees: [], teams: [], projects: [] }, today: new Date().toISOString().slice(0, 10), poll: null, cleanup: null };

const isAdmin = () => !!(state.me && state.me.is_admin);
const isManager = () => !!(state.me && (state.me.is_admin || state.me.is_pm));
const ROLE_LABEL = { admin: 'Administrator', pm: 'Project manager', employee: 'Employee' };

async function loadOrg() {
  state.org = await api('/api/org');
  return state.org;
}

const byId = (list, id) => list.find((x) => x.id === id);
const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
const personColor = (id) => SPEAKER_COLORS[id % SPEAKER_COLORS.length];
const view = document.getElementById('view');

async function loadStatus() {
  state.status = await api('/api/status');
  state.today = state.status.today;
  const set = (id, ok, okText, offText) => {
    document.getElementById(id).className = ok ? 'dot' : 'dot warn';
    document.getElementById(`${id}-t`).textContent = ok ? okText : offText;
  };
  set('st-claude', state.status.claude, (state.status.ai_label || 'connected').split(' · ')[0], 'no key');
  document.getElementById('st-claude-t').title = state.status.ai_label || '';
  set('st-speakers', state.status.speakers, 'active', 'no token');
  set('st-email', state.status.email, 'connected', 'test');
  set('st-jira', state.status.jira, state.status.jira_mock ? 'test' : 'connected', 'no token');
  const jiraRow = document.getElementById('st-jira').parentElement;
  if (state.status.jira_url && jiraRow.tagName !== 'A') {
    const link = el('a', { class: jiraRow.className, href: state.status.jira_url, target: '_blank', rel: 'noopener', title: 'Hap Jira' });
    link.append(...jiraRow.childNodes);
    jiraRow.replaceWith(link);
  }
}

async function refreshOpenCount() {
  try {
    const tasks = await api('/api/tasks?status=open');
    document.getElementById('open-count').textContent = tasks.length || '';
    if (isManager()) {
      // Tickets waiting for the PM: still to review or ready to send
      const tickets = await api('/api/tickets');
      document.getElementById('ticket-count').textContent = tickets.filter((t) => !t.jira_key && t.status !== 'done').length || '';
    }
  } catch { /* ignore */ }
}

function stopPolling() {
  clearInterval(state.poll);
  state.poll = null;
}

function poll(fn, ms = 2500) {
  stopPolling();
  state.poll = setInterval(fn, ms);
}

async function route() {
  stopPolling();
  if (state.cleanup) { state.cleanup(); state.cleanup = null; }
  const hash = location.hash.replace(/^#\/?/, '').split('?')[0];
  const [name, id] = hash.split('/');
  const key = name === 'meeting' ? 'meetings' : (name || 'home');
  document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.route === key));

  view.replaceChildren(el('div', { class: 'page' }, el('div', { class: 'thinking' }, el('span', { class: 'spinner' }), 'Loading…')));
  try {
    if (name === 'meeting' && id) await renderMeeting(Number(id));
    else if (name === 'meetings') await renderMeetings();
    else if (name === 'tasks') await renderTasks();
    else if (name === 'tickets' && isManager()) await renderTickets();
    else if (name === 'ask') renderAsk();
    else if (name === 'team' && isAdmin()) await renderTeam();
    else if (name === 'outbox' && isAdmin()) await renderOutbox();
    else if (name === 'vocabulary' && isAdmin()) await renderVocabulary();
    else await renderHome();
  } catch (e) {
    view.replaceChildren(el('div', { class: 'page' }, notice('error', 'alert', e.message)));
  }
  refreshOpenCount();
}

window.addEventListener('hashchange', () => { route(); view.focus({ preventScroll: true }); window.scrollTo(0, 0); });

/* ================= Shared pieces ================= */

function notice(kind, iconName, ...content) {
  return el('div', { class: `notice ${kind}` }, icon(iconName, 18), el('div', { class: 'grow' }, ...content));
}

function keyNotice() {
  if (state.status.claude) return null;
  return notice('warn', 'key',
    el('strong', { text: 'AI is not connected. ' }),
    'Transcription works, but for summaries, decisions, tasks and questions add ',
    el('code', { class: 'mono', text: 'GROQ_API_KEY=gsk_…' }),
    ' (free, for testing) or ',
    el('code', { class: 'mono', text: 'ANTHROPIC_API_KEY=sk-ant-…' }),
    ' to the .env file and restart the server.');
}

function speakerColors(lines) {
  const map = new Map();
  for (const l of lines) if (l.speaker && !map.has(l.speaker)) map.set(l.speaker, SPEAKER_COLORS[map.size % SPEAKER_COLORS.length]);
  return map;
}

function assigneeSelect(task, onDone) {
  const people = state.org.employees.filter((e) => e.active || e.id === task.assignee_id);
  const sel = el('select', {
    class: 'assign', 'aria-label': `Assignee for: ${task.description}`,
    onchange: async () => {
      sel.disabled = true;
      try {
        const id = sel.value ? Number(sel.value) : null;
        const updated = await api(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({ assignee_id: id, set_assignee: true }) });
        Object.assign(task, updated);
        toast(id ? `Task assigned to ${updated.assignee_name}` : 'Unlinked from the employee.');
        onDone();
      } catch (e) { toast(e.message); sel.disabled = false; }
    },
  },
  el('option', { value: '', text: task.assignee_id ? '— Not linked —' : `${task.assignee || 'Unassigned'} (not linked)` }),
  people.map((e) => el('option', { value: e.id, text: e.full_name, selected: e.id === task.assignee_id })));
  return sel;
}

/** Add or edit a task (PM only). */
function taskForm(meetingId, task, reload) {
  const t = task || { description: '', assignee_id: null, due_date: null };
  const desc = el('textarea', { rows: '3', maxlength: '1000', required: true, placeholder: 'What needs to be done?' });
  desc.value = t.description;
  const who = el('select', {},
    el('option', { value: '', text: t.assignee && !t.assignee_id ? `${t.assignee} (not linked)` : '— Unassigned —' }),
    state.org.employees.filter((e) => e.active || e.id === t.assignee_id)
      .map((e) => el('option', { value: e.id, text: e.full_name, selected: e.id === t.assignee_id })));
  const due = el('input', { type: 'date', value: t.due_date || '' });
  openForm(task ? 'Edit task' : 'New task', [
    el('div', { class: 'field-grid' },
      field('Task', desc),
      field('Assignee', who, { wide: false }),
      field('Due date', due, { wide: false, hint: t.due_date_text ? `Said in the meeting: “${t.due_date_text}”` : null })),
  ], {
    saveText: task ? 'Save' : 'Add task',
    onSave: async () => {
      const assignee = who.value ? Number(who.value) : null;
      if (task) {
        await api(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({
          description: desc.value, due_date: due.value || null, set_due_date: true,
          ...(assignee !== task.assignee_id ? { assignee_id: assignee, set_assignee: true } : {}) }) });
        toast('Task saved.');
      } else {
        await api(`/api/meetings/${meetingId}/tasks`, { method: 'POST', body: JSON.stringify({
          description: desc.value, due_date: due.value || null, assignee_id: assignee }) });
        toast('Task added.');
      }
      reload();
    },
    onDelete: task && !task.jira_key ? async () => {
      if (!confirm(`Delete the task “${task.description}”?`)) return false;
      await api(`/api/tasks/${task.id}`, { method: 'DELETE' });
      toast('Task deleted.');
      reload();
    } : null,
  });
}

/** Where a task stands as a ticket: part of one (made on the Tasks page) or an older ticket itself. */
function ticketPill(task) {
  if (task.merged_into) {
    const key = task.ticket_jira_key;
    return el('a', {
      class: key ? 'jira-link' : 'ticket-pill', href: `#/tickets?tab=${key ? 'sent' : 'send'}`,
      title: `In the ticket: ${task.ticket_title || ''}`,
    }, key ? `In ${key}` : 'In a ticket');
  }
  if (task.is_ticket && !task.jira_key) return el('a', { class: 'ticket-pill', href: '#/tickets', text: 'Ticket' });
  return null;
}

const inTicket = (t) => !!(t.merged_into || t.is_ticket);

function taskRow(task, { showMeeting = false, onChange, assignable = false, readOnly = false, manage = null, pick = null, onEdit = null, compact = false } = {}) {
  const row = el('div', { class: `task${task.status === 'done' ? ' is-done' : ''}${pick && pick.selected.has(task.id) ? ' is-picked' : ''}` });
  // Selection mode (Tasks page): the checkbox picks tasks for a ticket instead of marking them done
  const box = pick ? el('input', {
    type: 'checkbox', class: 'pick', checked: pick.selected.has(task.id), disabled: inTicket(task),
    'aria-label': `Select for a ticket: ${task.description}`, title: inTicket(task) ? 'Already part of a ticket' : 'Select for a ticket',
    onchange: (e) => { row.classList.toggle('is-picked', e.target.checked); pick.toggle(task, e.target.checked); },
  }) : el('input', {
    type: 'checkbox', checked: task.status === 'done', 'aria-label': `Mark as done: ${task.description}`,
    disabled: readOnly, title: readOnly ? 'Only the assignee or the PM can change this' : null,
    onchange: async () => {
      box.disabled = true;
      try {
        const updated = await api(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({ status: box.checked ? 'done' : 'open' }) });
        Object.assign(task, updated);
        row.replaceWith(taskRow(task, { showMeeting, onChange, assignable, readOnly, manage, pick, onEdit, compact }));
        refreshOpenCount();
        if (onChange) onChange(task);
      } catch (e) { box.checked = !box.checked; box.disabled = false; toast(e.message); }
    },
  });
  const name = task.assignee_name || task.assignee;
  const who = assignable && state.org.employees.length
    ? assigneeSelect(task, () => row.replaceWith(taskRow(task, { showMeeting, onChange, assignable, readOnly, manage, pick, onEdit, compact })))
    : name
      ? el('span', { class: 'who', title: task.assignee_id ? 'Linked to the employee list' : 'Name from the transcript' },
        task.assignee_id ? el('span', { class: 'dot', style: { background: personColor(task.assignee_id) } }) : icon('user', 14), name)
      : el('span', { class: 'subtle', text: 'Unassigned' });
  const meta = el('div', { class: 'meta' },
    who,
    duePill(task),
    task.due_date_text ? el('span', { class: 'subtle', text: `“${task.due_date_text}”` }) : null,
    task.jira_key ? el('a', { class: 'jira-link', href: task.jira_url, target: '_blank', rel: 'noopener', text: task.jira_key, title: 'Open in Jira' }) : null,
    ticketPill(task),
    !task.jira_key && task.jira_error ? el('span', { class: 'jira-error', text: `Jira: ${task.jira_error}` }) : null,
    showMeeting ? el('a', { href: `#/meeting/${task.meeting_id}`, class: 'small', text: task.meeting_title }) : null,
  );
  row.append(box, el('div', { class: 'grow' }, el('span', { class: 'desc', text: task.description }), meta));
  if (pick) {
    // Tasks page: the checkbox only selects; "done" is its own button so it can't be clicked by accident
    const done = task.status === 'done';
    row.append(el('div', { class: 'task-actions' },
      !inTicket(task) && !done ? el('button', {
        type: 'button', class: 'btn btn-sm', title: 'Make a ticket from this task alone', onclick: () => pick.single(task),
      }, icon('jira', 14, 2.2), 'Ticket') : null,
      onEdit && !task.jira_key && !task.ticket_jira_key ? el('button', {
        type: 'button', class: compact ? 'icon-btn' : 'btn btn-sm', title: task.merged_into ? 'Edit the ticket this task is part of' : 'Edit the task',
        'aria-label': task.merged_into ? 'Edit ticket' : 'Edit task', onclick: () => onEdit(task),
      }, icon('edit', compact ? 16 : 14, 2), compact ? null : (task.merged_into || task.is_ticket ? 'Edit ticket' : 'Edit')) : null,
      el('button', {
        type: 'button', class: `icon-btn${done ? ' is-on' : ''}`, 'aria-label': done ? `Reopen: ${task.description}` : `Mark as done: ${task.description}`,
        title: done ? 'Done — click to reopen' : 'Mark as done',
        onclick: async (e) => {
          if (!done && !confirm(`Mark “${task.description}” as done?`)) return;
          e.currentTarget.disabled = true;
          try {
            const updated = await api(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({ status: done ? 'open' : 'done' }) });
            Object.assign(task, updated);
            toast(done ? 'Task reopened.' : 'Task marked as done.');
            refreshOpenCount();
            if (onChange) onChange(task);
          } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
        },
      }, icon(done ? 'retry' : 'tick', 16, 2.2))));
  } else if (manage && !task.jira_key) {
    row.append(el('div', { class: 'task-actions' },
      el('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Edit task', title: 'Edit', onclick: () => taskForm(manage.meetingId, task, manage.reload) }, icon('edit', 16))));
  }
  return row;
}

function meetingRow(m) {
  const d = parseDate(m.meeting_date);
  const bits = [fmtDuration(m.duration_sec)];
  if (m.status === 'done' || m.task_count || m.decision_count) {
    bits.push(`${m.task_count} task${m.task_count === 1 ? '' : 's'}`, `${m.decision_count} decision${m.decision_count === 1 ? '' : 's'}`);
  } else if (m.status === 'error') bits.push('processing failed');
  else if (isBusy(m)) bits.push(m.note || 'processing');
  else if (m.status === 'needs_key') bits.push('transcript only');
  if (m.language) bits.push(m.language.toUpperCase());
  if (m.project_name || m.team_name) bits.unshift(m.project_name || m.team_name);
  return el('a', { class: 'meeting-row', href: `#/meeting/${m.id}` },
    el('div', { class: 'date-tile' }, el('b', { text: d.getDate() }), el('span', { text: MONTHS[d.getMonth()] })),
    el('div', { class: 'grow' },
      el('span', { class: 'name', text: m.title }),
      el('span', { class: 'small subtle', text: bits.filter(Boolean).join(' · ') })),
    statusChip(m),
    el('span', { class: 'row-arrow' }, icon('arrow', 16, 2)));
}

/* ---------- Ask (RAG) ---------- */

const EXAMPLES = ['What did we decide about the budget?', 'Which tasks does Arta have?', 'What is still unresolved?'];

function renderAnswer(container, res) {
  const parts = res.answer.split(/(\[\d+\])/g).map((p) => (/^\[\d+\]$/.test(p) ? el('span', { class: 'cite', text: p }) : p));
  const used = new Set((res.answer.match(/\[(\d+)\]/g) || []).map((x) => Number(x.slice(1, -1))));
  const sources = res.ai ? res.sources.filter((s) => used.has(s.n)) : res.sources;
  container.replaceChildren(el('div', { class: 'answer' },
    el('div', { class: 'answer-mark' }, icon('wave', 16, 2.2)),
    el('div', { class: 'answer-body' },
      el('p', { class: 'answer-text' }, ...parts),
      sources.length ? el('div', { class: 'source-list' }, sources.map((s) => el('a', {
        class: 'source', href: `#/meeting/${s.meeting_id}${s.start != null ? `?t=${Math.floor(s.start)}` : ''}`,
      },
      el('span', { class: 'n', text: `[${s.n}]` }),
      el('span', {},
        el('span', { class: 'meta', text: `${s.meeting_title} · ${fmtShort(s.meeting_date)}${s.start != null ? ` · ${fmtClock(s.start)}` : ''}` }),
          (s.speaker ? `${s.speaker}: ` : '') + s.text)))) : null)));
}

function askBox({ onAsk, autofocus = false }) {
  const input = el('input', { id: 'ask', type: 'text', placeholder: 'Ask about any meeting… e.g. What did we decide about the budget last month?', autocomplete: 'off' });
  const send = el('button', { type: 'submit', 'aria-label': 'Send question' }, icon('arrow', 18, 2.2));
  const form = el('form', {
    class: 'ask-box',
    onsubmit: (e) => { e.preventDefault(); const q = input.value.trim(); if (q) onAsk(q, input, send); },
  }, icon('search', 20, 2), input, send);
  if (autofocus) setTimeout(() => input.focus(), 0);
  const chips = el('div', { class: 'chips' }, EXAMPLES.map((q) => el('button', {
    type: 'button', class: 'chip', text: q, onclick: () => { input.value = q; form.requestSubmit(); },
  })));
  return { form, input, chips };
}

async function runAsk(q, input, send, out) {
  input.disabled = true; send.disabled = true;
  out.replaceChildren(el('div', { class: 'thinking' }, el('span', { class: 'spinner' }), 'Searching past meetings…'));
  try {
    renderAnswer(out, await api('/api/ask', { method: 'POST', body: JSON.stringify({ question: q }) }));
  } catch (e) {
    out.replaceChildren(notice('error', 'alert', e.message));
  } finally {
    input.disabled = false; send.disabled = false;
  }
}

/* ================= Views ================= */

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}

/** Card title with a small icon badge. */
function cardTitle(iconName, text, tone = '') {
  return el('div', { class: 'card-title' }, el('span', { class: `badge ${tone}` }, icon(iconName, 18, 2)), el('h2', { text }));
}

function pageHead(eyebrow, title, ...actions) {
  return el('header', { class: 'page-head' },
    el('div', { class: 'title' }, el('span', { class: 'eyebrow', text: eyebrow }), el('h1', { text: title })),
    actions.length ? el('div', { class: 'actions' }, ...actions) : null);
}

function newMeetingButtons() {
  if (!isManager()) return [];
  return [
    el('button', { type: 'button', class: 'btn', onclick: () => openUpload('file') }, icon('upload', 16, 2), 'Upload audio'),
    el('button', { type: 'button', class: 'btn btn-primary', onclick: () => openUpload('record') }, el('span', { class: 'rec-dot' }), 'Record'),
  ];
}

/* ---------- Home ---------- */

async function renderHome() {
  const [meetings, report, tasks] = await Promise.all([api('/api/meetings'), api('/api/report'), api('/api/tasks?status=open')]);

  const out = el('div', { class: 'hero-answer', hidden: true });
  const ask = askBox({ onAsk: (q, i, s) => { out.hidden = false; runAsk(q, i, s, out); } });
  const done = meetings.filter((m) => m.status === 'done').length;
  const hero = el('section', { class: 'hero', 'aria-label': 'Ask the archive' },
    el('div', { class: 'hero-top' },
      el('div', { class: 'title' },
        el('span', { class: 'eyebrow', text: fmtLong(state.today) }),
        el('h1', { text: `${greeting()}, ${state.me.full_name.split(' ')[0]}` }),
        el('p', { class: 'hero-sub', text: meetings.length
          ? `${meetings.length} meeting${meetings.length === 1 ? '' : 's'} in the archive${done ? `, ${done} analyzed` : ''}${tasks.length ? ` · ${tasks.length} open task${tasks.length === 1 ? '' : 's'}` : ''}. Ask anything about them.`
          : 'Add your first meeting, then ask anything about it.' })),
      isManager() ? el('div', { class: 'actions' },
        el('button', { type: 'button', class: 'btn btn-glass', onclick: () => openUpload('file') }, icon('upload', 16, 2), 'Upload audio'),
        el('button', { type: 'button', class: 'btn btn-yellow', onclick: () => openUpload('record') }, el('span', { class: 'rec-dot' }), 'Record')) : null),
    el('label', { for: 'ask', class: 'sr-only', text: 'Ask about any past meeting' }),
    ask.form, ask.chips, out);

  const processingSlot = el('div', { class: 'col-2 stack' });
  const meetingsSlot = el('div', { class: 'col-3 stack' });

  const paint = (list) => {
    // Employees don't add meetings, so they get their tasks there instead
    processingSlot.replaceChildren(isManager() ? processingCard(list) : openTasksCard(tasks));
    meetingsSlot.replaceChildren(recentMeetingsCard(list));
  };
  paint(meetings);

  view.replaceChildren(el('div', { class: 'page' },
    hero,
    isAdmin() ? keyNotice() : null,
    el('div', { class: 'row' }, el('div', { class: 'col-3 stack' }, reportCard(report)), processingSlot),
    el('div', { class: 'row' }, meetingsSlot, isManager() ? el('div', { class: 'col-2 stack' }, openTasksCard(tasks)) : null),
  ));

  if (meetings.some(isBusy)) {
    poll(async () => {
      const list = await api('/api/meetings');
      paint(list);
      if (!list.some(isBusy)) { stopPolling(); route(); }
    });
  }
}

function reportCard(r) {
  const card = el('section', { class: 'card', 'aria-label': 'Task report' });
  if (!r) {
    card.append(cardTitle('check', 'Pre-meeting report'),
      el('div', { class: 'empty' }, el('strong', { text: 'No tasks yet' }),
        'After the first meeting, this shows how many tasks are done and how many are late.'));
    return card;
  }
  const total = r.done + r.late + r.open;
  card.append(
    el('div', { class: 'card-head' },
      cardTitle('check', 'From the last meeting'),
      el('a', { class: 'small link-pill', href: `#/meeting/${r.meeting.id}`, text: `${r.meeting.title} · ${fmtShort(r.meeting.meeting_date)}` })),
    el('div', { class: 'stats' },
      el('div', { class: 'stat done' }, el('b', { text: r.done }), el('span', { text: 'done' })),
      el('div', { class: 'stat late' }, el('b', { text: r.late }), el('span', { text: 'late' })),
      el('div', { class: 'stat open' }, el('b', { text: r.open }), el('span', { text: 'in progress' }))),
    el('div', { class: 'bar', role: 'img', 'aria-label': `${r.done} done, ${r.late} late, ${r.open} in progress` },
      r.done ? el('div', { style: { flex: r.done, background: 'var(--green)' } }) : null,
      r.late ? el('div', { style: { flex: r.late, background: 'var(--orange)' } }) : null,
      r.open ? el('div', { style: { flex: r.open, background: 'var(--accent)' } }) : null,
      total === 0 ? el('div', { style: { flex: 1, background: 'var(--surface-3)' } }) : null),
  );
  if (r.late_tasks.length) {
    card.append(el('div', { class: 'list' }, r.late_tasks.map((t) => el('div', { class: 'list-row' },
      duePill(t), el('span', { class: 'grow', text: t.description }), el('span', { class: 'small muted', text: t.assignee_name || '' })))));
  } else if (total > 0) {
    card.append(el('p', { class: 'small muted', text: 'No late tasks.' }));
  }
  return card;
}

function pipelineSteps(m) {
  const order = ['queued', 'transcribing', 'diarizing', 'extracting', 'done'];
  const at = order.indexOf(m.status);
  const st = (i) => (at > i ? 'done' : at === i ? 'active' : 'todo');
  const speakers = state.status.speakers;
  const claude = state.status.claude;
  const steps = [
    { label: 'Transcription', s: m.status === 'queued' ? 'todo' : st(1), meta: m.status === 'transcribing' ? `${Math.round((m.progress || 0) * 100)}%` : '' },
    { label: 'Who is speaking', s: speakers ? st(2) : 'skip', meta: speakers ? '' : 'no token' },
    { label: 'Decisions and tasks', s: claude ? st(3) : 'skip', meta: claude ? '' : 'no key' },
  ];
  return el('ol', { class: 'steps' }, steps.map((x) => el('li', { class: `step ${x.s}` },
    el('span', { class: 'step-dot' }, x.s === 'done' ? icon('tick', 12, 3.5) : null),
    el('span', { text: x.label }), el('span', { class: 'meta', text: x.meta }))));
}

function processingCard(list) {
  const busy = list.filter(isBusy);
  const card = el('section', { class: 'card', 'aria-label': 'Processing' });
  if (!busy.length) {
    card.append(
      cardTitle('wave', 'Nothing in progress', 'yellow'),
      el('p', { class: 'small muted', text: 'Upload a recording or record the meeting straight from the microphone. The system transcribes it and extracts decisions and tasks automatically.' }),
      el('div', { class: 'actions' }, ...newMeetingButtons().reverse()),
    );
    return card;
  }
  const m = busy[0];
  card.append(...nn(
    el('div', { class: 'stack', style: { gap: '6px' } },
      el('span', { class: 'eyebrow accent', text: m.status === 'queued' ? 'Queued' : 'Processing' }),
      el('a', { href: `#/meeting/${m.id}` }, el('h2', { text: m.title })),
      el('span', { class: 'small subtle', text: [fmtDuration(m.duration_sec), m.language && m.language.toUpperCase(), m.model].filter(Boolean).join(' · ') })),
    el('div', { class: 'progress' }, el('div', { style: { width: `${Math.round((m.status === 'transcribing' ? m.progress : m.status === 'queued' ? 0 : 1) * 100)}%` } })),
    pipelineSteps(m),
    m.note ? el('p', { class: 'small subtle', text: m.note }) : null,
    busy.length > 1 ? el('p', { class: 'small subtle', text: `+${busy.length - 1} more in the queue` }) : null,
  ));
  return card;
}

function recentMeetingsCard(list) {
  const card = el('section', { class: 'card', 'aria-label': 'Recent meetings', style: { gap: '4px' } },
    el('div', { class: 'card-head', style: { marginBottom: '8px' } }, cardTitle('mic', 'Recent meetings'),
      list.length > 5 ? el('a', { href: '#/meetings', class: 'small', text: 'View all' }) : null));
  if (!list.length) {
    card.append(el('div', { class: 'empty' }, el('strong', { text: 'No meetings yet' }), 'Add the first one with “Upload audio” or “Record”.'));
  } else card.append(...list.slice(0, 5).map(meetingRow));
  return card;
}

function openTasksCard(tasks) {
  const card = el('section', { class: 'card', 'aria-label': 'Open tasks', style: { gap: '4px' } },
    el('div', { class: 'card-head', style: { marginBottom: '4px' } }, cardTitle('check', isManager() ? 'Open tasks' : 'My tasks', 'yellow'),
      el('a', { href: '#/tasks', class: 'small', text: tasks.length ? `${tasks.length} total` : '' })));
  if (!tasks.length) card.append(el('div', { class: 'empty' }, el('strong', { text: 'No open tasks' }), 'Tasks from meetings appear here.'));
  else card.append(el('div', {}, tasks.slice(0, 6).map((t) => taskRow(t, { showMeeting: true }))));
  return card;
}

/* ---------- Meetings ---------- */

async function renderMeetings() {
  const list = await api('/api/meetings');
  const slot = el('section', { class: 'card', style: { gap: '4px' } });
  const paint = (l) => slot.replaceChildren(...(l.length ? l.map(meetingRow)
    : [el('div', { class: 'empty' }, el('strong', { text: 'No meetings yet' }), 'Add the first one with “Upload audio” or “Record”.')]));
  paint(list);
  view.replaceChildren(el('div', { class: 'page' }, pageHead(`${list.length} total`, 'Meetings', ...newMeetingButtons()), slot));
  if (list.some(isBusy)) poll(async () => { const l = await api('/api/meetings'); paint(l); if (!l.some(isBusy)) stopPolling(); });
}

/* ---------- Tasks ---------- */

async function renderTasks() {
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  let meetingFilter = Number(params.get('meeting')) || null;
  let [all] = await Promise.all([api('/api/tasks'), loadOrg()]);
  let filter = 'open';
  let person = '';
  // Selection mode: pick one or more tasks and the AI writes ONE ticket from them
  const picking = isManager();  // the PM's checkboxes select tasks for a ticket
  const listSlot = el('section', { class: 'card', style: { gap: 0 } });
  const filterBar = el('div', { class: 'filter', role: 'group', 'aria-label': 'Filter tasks' });
  const ownerOk = (t) => (!person || (person === 'none' ? !t.assignee_id : t.assignee_id === Number(person)))
    && (!meetingFilter || t.meeting_id === meetingFilter);
  const personSel = el('select', { class: 'assign', 'aria-label': 'Filter by person', style: { minHeight: '44px', maxWidth: '260px' },
    onchange: () => { person = personSel.value; paint(); } },
  el('option', { value: '', text: 'Everyone' }),
  el('option', { value: 'none', text: 'Not linked to an employee' }),
  state.org.employees.map((e) => el('option', { value: e.id, text: e.full_name })));

  const reloadTasks = async () => { all = await api('/api/tasks'); refreshOpenCount(); paint(); };
  const picker = taskPicker(reloadTasks);
  const { pick, footer } = picker;
  // Edit opens the ticket when the task is part of one (all ticket editing happens here)
  const edit = (t) => {
    const target = t.merged_into || t.id;
    ticketEditForm(target, all.filter((x) => x.id === target || x.merged_into === target), reloadTasks).catch((e) => toast(e.message));
  };

  const paint = () => {
    const mine = all.filter(ownerOk);
    const shown = mine.filter((t) => filter === 'all' || t.status === filter);
    filterBar.replaceChildren(...[['open', 'Open'], ['done', 'Done'], ['all', 'All']].map(([k, label]) => el('button', {
      type: 'button', class: filter === k ? 'active' : '', 'aria-pressed': String(filter === k),
      text: `${label} ${mine.filter((t) => k === 'all' || t.status === k).length}`,
      onclick: () => { filter = k; paint(); },
    })));
    listSlot.replaceChildren(...(shown.length ? shown.map((t) => taskRow(t, {
      showMeeting: true, onChange: paint, assignable: isManager(), pick: picking ? pick : null,
      onEdit: isManager() ? edit : null,
    }))
      : [el('div', { class: 'empty' }, el('strong', { text: filter === 'done' ? 'No completed tasks yet' : 'No tasks here' }),
        'Tasks are created automatically from meetings.')]));
    picker.paintFooter();
  };
  paint();
  const meetingTitle = meetingFilter && (all.find((t) => t.meeting_id === meetingFilter) || {}).meeting_title;
  view.replaceChildren(el('div', { class: 'page' },
    pageHead(isManager() ? 'From your projects’ meetings' : 'Assigned to you', isManager() ? 'Tasks' : 'My tasks',
      isManager() ? personSel : null, filterBar),
    isManager() ? el('p', { class: 'muted', text: 'Tick the tasks that belong together and click “Make ticket”: the AI writes one Jira ticket from them. ✓ on the right marks a task as done.' }) : null,
    meetingTitle ? el('div', { class: 'chips' }, el('button', { type: 'button', class: 'chip active', title: 'Clear filter',
      onclick: () => { meetingFilter = null; location.hash = '#/tasks'; } }, `Only: ${meetingTitle}`, icon('x', 14, 2.2))) : null,
    listSlot, footer));
  const editId = Number(params.get('edit'));
  if (editId && isManager()) edit({ id: editId });
}

/* ---------- Ask ---------- */

function renderAsk() {
  const chat = el('div', { class: 'chat' });
  const ask = askBox({
    autofocus: true,
    onAsk: (q, input, send) => {
      const out = el('div');
      chat.append(el('div', { class: 'chat-q', text: q }), out);
      input.value = '';
      runAsk(q, input, send, out).then(() => { out.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); input.focus(); });
    },
  });
  view.replaceChildren(el('div', { class: 'page', style: { maxWidth: '860px' } },
    pageHead('The team’s memory', 'Ask the archive'),
    keyNotice(),
    el('p', { class: 'muted', text: 'Searches the transcripts, decisions and tasks of every meeting and answers with the exact source.' }),
    el('section', { class: 'card' }, ask.form, ask.chips),
    chat));
}

/* ---------- Meeting detail ---------- */

async function renderMeeting(id) {
  stopPolling();
  if (state.cleanup) { state.cleanup(); state.cleanup = null; }
  const [m] = await Promise.all([api(`/api/meetings/${id}`), loadOrg()]);
  const startAt = Number(new URLSearchParams(location.hash.split('?')[1] || '').get('t')) || null;

  const actions = [];
  const unsent = (m.tickets || []).filter((t) => !t.jira_key && t.status !== 'done');
  // Tickets are made here (select action items) and sent from the Tickets page, never straight to Jira
  if (m.can_manage && unsent.length) {
    actions.push(el('a', { class: 'btn btn-primary', href: `#/tickets?meeting=${m.id}` }, icon('jira', 16, 2), `Tickets to send (${unsent.length})`));
  }
  if (m.can_manage && !isBusy(m) && m.transcript && state.status.claude) {
    actions.push(el('button', { type: 'button', class: 'btn', onclick: () => {
      if (m.tasks.length && !confirm('A new analysis replaces the decisions and the action items you haven’t touched. Items you edited and tickets are kept. Continue?')) return;
      retry(m.id);
    } }, icon('retry', 16, 2), 'Analyze again'));
  }
  if (m.can_manage && !['transcribing', 'diarizing', 'extracting'].includes(m.status)) {
    actions.push(el('button', {
      type: 'button', class: 'btn btn-danger', onclick: async () => {
        if (!confirm(`Delete the meeting “${m.title}” with its tasks and audio?`)) return;
        try { await api(`/api/meetings/${m.id}`, { method: 'DELETE' }); toast('Meeting deleted.'); location.hash = '#/meetings'; } catch (e) { toast(e.message); }
      },
    }, icon('trash', 16, 2), 'Delete'));
  }

  const lines = m.transcript ? (m.transcript.utterances || m.transcript.segments) : [];
  const colors = speakerColors(lines);
  const meta = el('div', { class: 'meta-line' },
    el('span', { text: fmtLong(m.meeting_date) }),
    m.duration_sec ? [el('span', { class: 'sep', text: '/' }), el('span', { text: fmtDuration(m.duration_sec) })] : null,
    m.language ? el('span', { class: 'tag', text: m.language }) : null,
    colors.size ? el('span', { text: `${colors.size} speaker${colors.size === 1 ? '' : 's'}` }) : null,
    m.team_name ? el('span', { class: 'tag-team', text: m.team_name }) : null,
    m.project_name ? el('span', { class: 'tag-project', text: m.project_name }) : null,
    statusChip(m));

  const page = el('div', { class: 'page' },
    el('div', { class: 'card-head' }, el('a', { class: 'back', href: '#/' }, icon('back', 18, 2), 'Dashboard'), el('div', { class: 'actions' }, ...actions)),
    el('header', { class: 'stack', style: { gap: '12px' } }, el('h1', { text: m.title, style: { fontSize: '36px' } }), meta));

  if (isBusy(m)) {
    page.append(el('section', { class: 'card', style: { maxWidth: '560px' } },
      el('span', { class: 'eyebrow accent', text: 'Processing' }),
      el('div', { class: 'progress' }, el('div', { style: { width: `${Math.round((m.status === 'transcribing' ? m.progress : m.status === 'queued' ? 0 : 1) * 100)}%` } })),
      pipelineSteps(m),
      el('p', { class: 'small subtle', text: m.note || 'You can close this page; processing continues in the background.' })));
    view.replaceChildren(page);
    let last = `${m.status}:${Math.round(m.progress * 100)}`;
    poll(async () => {
      const now = await api(`/api/meetings/${id}`);
      const key = `${now.status}:${Math.round(now.progress * 100)}`;
      if (key !== last) { last = key; stopPolling(); renderMeeting(id); }
    }, 2000);
    return;
  }

  if (m.status === 'error') {
    page.append(notice('error', 'alert', el('strong', { text: 'Processing failed. ' }), m.error || '',
      m.can_manage ? el('div', { style: { marginTop: '10px' } }, el('button', { type: 'button', class: 'btn btn-sm', onclick: () => retry(m.id) }, 'Try again')) : null));
  }
  if (m.status === 'needs_key') {
    page.append(notice('warn', 'key', m.note || 'The transcript is ready.',
      state.status.claude && m.can_manage ? el('div', { style: { marginTop: '10px' } },
        el('button', { type: 'button', class: 'btn btn-primary btn-sm', onclick: () => retry(m.id) }, 'Extract decisions and tasks')) : null));
  } else if (m.note && m.status === 'done') {
    page.append(notice('', 'alert', m.note));
  }

  const player = m.source !== 'json' && m.transcript ? buildPlayer(m, lines, startAt) : null;
  if (player) page.append(player.node);

  // Lines Claude corrected keep the recognizer's original text in "raw"
  const fixedCount = lines.filter((l) => l.raw).length;
  const txtNodes = [];
  let showRaw = false;
  const rawToggle = fixedCount ? el('button', {
    type: 'button', class: 'chip', 'aria-pressed': 'false',
    title: 'Compare with the text as the speech recognizer heard it, before correction',
    onclick: () => {
      showRaw = !showRaw;
      rawToggle.setAttribute('aria-pressed', String(showRaw));
      rawToggle.classList.toggle('active', showRaw);
      rawToggle.textContent = showRaw ? 'Show corrected' : `Show original (${fixedCount} line${fixedCount === 1 ? '' : 's'} corrected)`;
      txtNodes.forEach(([l, n]) => { n.textContent = showRaw ? l.raw : l.text; });
    },
  }, `Show original (${fixedCount} line${fixedCount === 1 ? '' : 's'} corrected)`) : null;

  const transcriptCard = el('section', { class: 'card', 'aria-label': 'Transcript' },
    el('div', { class: 'card-head' }, el('h2', { text: 'Transcript' }),
      colors.size ? el('div', { class: 'speakers' }, [...colors].map(([name, c]) => el('span', { class: 'speaker' },
        el('span', { class: 'dot', style: { background: c } }), name))) : null),
    rawToggle ? el('div', { class: 'chips' }, rawToggle) : null);
  const lineNodes = [];
  const canEdit = m.can_manage && !isBusy(m);
  if (canEdit && lines.length) {
    transcriptCard.append(el('p', { class: 'small subtle edit-hint' }, icon('edit', 14, 2),
      'Double-click a line (or any Albanian quote) to fix words the recognizer got wrong. The system learns your fixes and won’t repeat the same mistakes.'));
  }

  // Fix one line; the changed words are learned (see app/learning.py)
  function editLine(index, node) {
    const l = lines[index];
    window.getSelection().removeAllRanges();
    const input = el('textarea', { class: 'line-input', rows: Math.max(2, Math.ceil(l.text.length / 70)), 'aria-label': 'Fix the line' });
    input.value = l.text;
    const cancel = () => { editor.replaceWith(node); node.focus(); };
    const save = async () => {
      const text = input.value.trim();
      if (!text || text === l.text) { cancel(); return; }
      saveBtn.disabled = true;
      try {
        const r = await api(`/api/meetings/${m.id}/lines/${index}`, { method: 'PATCH', body: JSON.stringify({ text }) });
        const learned = r.learned.map((f) => `“${f.wrong}” → “${f.right}”`).join(', ');
        await renderMeeting(m.id);
        if (r.others) offerFixAll(m.id, r);
        else toast(learned ? `Saved. Learned: ${learned}` : 'Line saved.');
      } catch (e) { toast(e.message); saveBtn.disabled = false; }
    };
    const saveBtn = el('button', { type: 'button', class: 'btn btn-sm btn-primary', onclick: save }, icon('tick', 14, 2.4), 'Save and learn');
    const editor = el('div', { class: 'line editing' },
      el('span', { class: 't', text: fmtClock(l.start) }),
      el('div', { class: 'body', style: { flex: '1' } },
        l.speaker ? el('span', { class: 'who', text: l.speaker, style: { color: colors.get(l.speaker) } }) : null,
        input,
        l.raw ? el('small', { class: 'subtle', text: `Recognizer heard: ${l.raw}` }) : null,
        el('div', { class: 'line-edit-actions' },
          el('small', { class: 'subtle', text: 'Enter = save · Esc = cancel' }),
          el('span', { class: 'spacer' }),
          el('button', { type: 'button', class: 'btn btn-sm', text: 'Cancel', onclick: cancel }),
          saveBtn)));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); save(); }
      if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });
    node.replaceWith(editor);
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }

  if (!lines.length) transcriptCard.append(el('div', { class: 'empty', text: 'No transcript.' }));
  else {
    const box = el('div', { class: 'transcript' });
    lines.forEach((l, index) => {
      const txt = el('span', { class: 'txt', text: l.text });
      if (l.raw) txtNodes.push([l, txt]);
      const node = el('button', {
        type: 'button', class: `line${l.raw ? ' fixed' : ''}${l.edited ? ' edited' : ''}`,
        onclick: (e) => {
          if (e.detail === 1 && player) player.seek(l.start);
        },
        ondblclick: () => { if (canEdit) editLine(index, node); },
        title: [l.raw ? `Original: ${l.raw}` : (player ? 'Play from here' : null), canEdit ? 'Double-click (or F2) to fix the words' : null].filter(Boolean).join(' · ') || null,
      },
      el('span', { class: 't', text: fmtClock(l.start) }),
      el('span', { class: 'body' },
        l.speaker ? el('span', { class: 'who', text: l.speaker, style: { color: colors.get(l.speaker) } }) : null,
        txt));
      if (canEdit) {
        node.addEventListener('keydown', (e) => { if (e.key === 'F2') { e.preventDefault(); editLine(index, node); } });
        let lastRight = 0;
        node.addEventListener('contextmenu', (e) => {
          const now = Date.now();
          if (now - lastRight < 600) { e.preventDefault(); lastRight = 0; editLine(index, node); } else lastRight = now;
        });
      }
      lineNodes.push([l.start, node]);
      box.append(node);
    });
    transcriptCard.append(box);
    if (player) player.onTime((t) => {
      let cur = null;
      for (const [s, n] of lineNodes) { if (s <= t + 0.2) cur = n; else break; }
      lineNodes.forEach(([, n]) => n.classList.toggle('now', n === cur));
      if (cur && player.playing()) {
        const top = cur.offsetTop - box.offsetTop;
        if (top < box.scrollTop || top > box.scrollTop + box.clientHeight - 80) box.scrollTop = top - 40;
      }
    });
  }

  const side = el('div', { class: 'col-2 stack' });
  // Action items grouped by person; the PM ticks the ones that belong together and makes a ticket
  const reloadMeeting = () => renderMeeting(m.id);
  const picker = m.can_manage ? taskPicker(reloadMeeting) : null;
  const editItem = (t) => {
    const target = t.merged_into || t.id;
    ticketEditForm(target, m.tasks.filter((x) => x.id === target || x.merged_into === target), reloadMeeting).catch((e) => toast(e.message));
  };
  const groups = new Map();
  for (const t of m.tasks) {
    const who = t.assignee_name || t.assignee || 'Unassigned';
    if (!groups.has(who)) groups.set(who, []);
    groups.get(who).push(t);
  }
  const tasksCard = el('section', { class: 'card', style: { gap: '4px' } },
    el('div', { class: 'card-head', style: { marginBottom: '4px' } },
      el('span', { class: 'eyebrow', text: 'Action items' }),
      el('div', { class: 'actions' },
        m.can_manage ? el('button', { type: 'button', class: 'btn btn-sm', onclick: () => taskForm(m.id, null, reloadMeeting) }, icon('plus', 14, 2.4), 'Add') : null,
        el('span', { class: 'mono small muted', text: m.tasks.length }))),
    m.can_manage && m.tasks.some((t) => !inTicket(t) && t.status !== 'done')
      ? el('p', { class: 'small subtle', text: 'Tick the items that belong together and click “Make ticket”.' }) : null,
    m.tasks.length ? [...groups].map(([who, items]) => el('div', { class: 'ai-group' },
      el('div', { class: 'ai-who' }, items[0].assignee_id ? el('span', { class: 'dot', style: { background: personColor(items[0].assignee_id) } }) : icon('user', 14), who),
      items.map((t) => taskRow(t, m.can_manage
        ? { pick: picker.pick, onEdit: editItem, onChange: reloadMeeting, compact: true }
        : { readOnly: t.assignee_id !== state.me.id }))))
      : el('p', { class: 'small subtle', text: 'No action items.' }),
    picker ? picker.footer : null);

  if (m.summary !== null && m.summary !== undefined) {
    side.append(...nn(
      el('section', { class: 'card' }, el('span', { class: 'eyebrow', text: 'Summary' }), el('p', { class: 'summary', text: m.summary })),
      tasksCard,
      // Work people reported as finished: shown for context, never turned into tickets
      (m.completed || []).length ? el('section', { class: 'card' },
        el('div', { class: 'card-head' }, el('span', { class: 'eyebrow', text: 'Already done' }),
          el('span', { class: 'small subtle', text: 'not tickets' })),
        el('ul', { class: 'done-list' }, m.completed.map((x) => el('li', {}, icon('tick', 14, 2.6), el('span', { text: x }))))) : null,
      el('section', { class: 'card' },
        el('div', { class: 'card-head' }, el('span', { class: 'eyebrow', text: 'Decisions' }), el('span', { class: 'mono small muted', text: m.decisions.length })),
        m.decisions.length ? m.decisions.map((d) => el('div', { class: 'decision' }, el('b', { text: d.description }), quote(d.quote, lines, player)))
          : el('p', { class: 'small subtle', text: 'No decisions made.' })),
      m.open_questions.length ? el('section', { class: 'card' }, el('span', { class: 'eyebrow', text: 'Open questions' }),
        el('div', { class: 'list' }, m.open_questions.map((q) => el('div', { class: 'list-row', text: q })))) : null,
    ));
  } else if (m.transcript) {
    side.append(el('section', { class: 'card' }, el('span', { class: 'eyebrow', text: 'Analysis' }),
      el('div', { class: 'empty' }, el('strong', { text: 'No summary yet' }), 'Decisions and tasks appear here once the AI analyzes the transcript.')));
    if (m.can_manage && !isBusy(m)) side.append(tasksCard);  // the PM can still add tasks by hand
  }

  page.append(el('div', { class: 'row' }, el('div', { class: 'col-3 stack' }, transcriptCard), side));
  view.replaceChildren(page);
}

/** After a fix: the same wrong words still appear in other lines of this meeting. */
function offerFixAll(meetingId, r) {
  const learned = r.learned.map((f) => `“${f.wrong}” → “${f.right}”`).join(', ');
  const n = r.others;
  openForm('Fix the same mistake everywhere?', [
    el('p', { text: `Learned: ${learned}.` }),
    el('p', { class: 'small muted', text: `${n} other line${n === 1 ? '' : 's'} in this meeting still contain${n === 1 ? 's' : ''} the same mistake. Fix ${n === 1 ? 'it' : 'them'} too? Learned corrections are also applied to future meetings automatically.` }),
  ], {
    saveText: `Fix ${n} more`,
    onSave: async () => {
      const res = await api(`/api/meetings/${meetingId}/apply-learned`, { method: 'POST' });
      toast(`Fixed ${res.changed} line${res.changed === 1 ? '' : 's'}.`);
      renderMeeting(meetingId);
    },
  });
}

function quote(text, lines, player) {
  if (!text) return null;
  // Find the transcript line the quote came from, so it can be played
  const needle = text.toLowerCase().replace(/[^\p{L}\p{N} ]/gu, '').slice(0, 40);
  const hit = needle.length >= 8 && lines.find((l) => l.text.toLowerCase().replace(/[^\p{L}\p{N} ]/gu, '').includes(needle));
  if (hit && player) {
    return el('button', { type: 'button', class: 'quote', onclick: () => player.seek(hit.start) },
      `“${text}” `, el('span', { class: 'mono', style: { color: 'var(--accent-text)' }, text: fmtClock(hit.start) }));
  }
  return el('span', { class: 'quote', text: `“${text}”${hit ? ` · ${fmtClock(hit.start)}` : ''}` });
}

function buildPlayer(m, lines, startAt) {
  const audio = el('audio', { src: `/api/meetings/${m.id}/audio`, preload: 'metadata' });
  const playBtn = el('button', { type: 'button', class: 'play', 'aria-label': 'Play' });
  const cur = el('span', { class: 'time', text: '00:00' });
  const total = el('span', { class: 'time subtle', text: fmtClock(m.duration_sec) });
  const N = 120;
  let seed = m.id * 9301 + 49297;
  const rand = () => ((seed = (seed * 9301 + 49297) % 233280) / 233280);
  const bars = Array.from({ length: N }, (_, i) => el('span', { style: { height: `${Math.round(8 + 28 * Math.abs(Math.sin(i * 0.37 + rand() * 2)) * (0.45 + rand() * 0.55))}px` } }));
  const wave = el('div', { class: 'wave', role: 'slider', tabindex: '0', 'aria-label': 'Position in the recording', 'aria-valuemin': '0', 'aria-valuemax': String(Math.round(m.duration_sec || 0)), 'aria-valuenow': '0' }, bars);
  const listeners = [];

  const dur = () => (Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (m.duration_sec || 0));
  const setIcon = () => {
    playBtn.replaceChildren(audio.paused
      ? el('span', { style: { display: 'inline-flex' } }, svgFill('M7 4.5v15l13-7.5z'))
      : el('span', { style: { display: 'inline-flex' } }, svgFill('M6 4h4v16H6zM14 4h4v16h-4z')));
    playBtn.setAttribute('aria-label', audio.paused ? 'Play' : 'Pause');
  };
  const paint = () => {
    const t = audio.currentTime, d = dur();
    cur.textContent = fmtClock(t);
    const on = d ? Math.round((t / d) * N) : 0;
    bars.forEach((b, i) => b.classList.toggle('on', i < on));
    wave.setAttribute('aria-valuenow', String(Math.round(t)));
    listeners.forEach((fn) => fn(t));
  };
  const seek = (t) => { audio.currentTime = Math.max(0, Math.min(t, dur())); audio.play().catch(() => {}); paint(); };

  playBtn.addEventListener('click', () => (audio.paused ? audio.play().catch((e) => toast(`Could not play the audio: ${e.message}`)) : audio.pause()));
  audio.addEventListener('play', setIcon);
  audio.addEventListener('pause', setIcon);
  audio.addEventListener('timeupdate', paint);
  audio.addEventListener('error', () => toast('The audio was not found or can’t be played in the browser.'));
  wave.addEventListener('click', (e) => { const r = wave.getBoundingClientRect(); seek(((e.clientX - r.left) / r.width) * dur()); });
  wave.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); seek(audio.currentTime + 5); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); seek(audio.currentTime - 5); }
  });
  setIcon();
  if (startAt !== null) audio.addEventListener('loadedmetadata', () => { audio.currentTime = startAt; paint(); }, { once: true });
  state.cleanup = () => { audio.pause(); audio.removeAttribute('src'); audio.load(); };

  return {
    node: el('section', { class: 'player', 'aria-label': 'Recording' }, playBtn, cur, wave, total, audio),
    seek,
    onTime: (fn) => listeners.push(fn),
    playing: () => !audio.paused,
  };
}

function svgFill(d) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('width', '16'); svg.setAttribute('height', '16'); svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'currentColor'); svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(ns, 'path'); p.setAttribute('d', d); svg.append(p);
  return svg;
}

async function retry(id) {
  try { await api(`/api/meetings/${id}/retry`, { method: 'POST' }); toast('Queued for processing.'); renderMeeting(id); } catch (e) { toast(e.message); }
}

/* ================= Team: employees, teams, projects ================= */

const formDlg = document.getElementById('form-dialog');
const formEl = document.getElementById('form-dialog-form');

/** Generic form dialog. fields: nodes; onSave(): Promise; onDelete(): Promise | null. */
function openForm(title, fields, { onSave, onDelete, saveText = 'Save' }) {
  const err = el('p', { class: 'form-error', role: 'alert' });
  const save = el('button', { type: 'submit', class: 'btn btn-primary', text: saveText });
  formEl.replaceChildren(
    el('div', { class: 'dialog-head' }, el('h2', { text: title }),
      el('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Close', onclick: () => formDlg.close() }, icon('x'))),
    ...fields, err,
    el('div', { class: 'dialog-actions' },
      onDelete ? el('button', { type: 'button', class: 'btn btn-danger', onclick: async () => {
        try { if ((await onDelete()) !== false) formDlg.close(); } catch (e) { err.textContent = e.message; }
      } }, icon('trash', 16, 2), 'Delete') : null,
      el('span', { class: 'spacer' }),
      el('button', { type: 'button', class: 'btn', text: 'Cancel', onclick: () => formDlg.close() }),
      save));
  formEl.onsubmit = async (e) => {
    e.preventDefault();
    err.textContent = '';
    save.disabled = true;
    try { await onSave(); formDlg.close(); } catch (ex) { err.textContent = ex.message; } finally { save.disabled = false; }
  };
  formDlg.showModal();
  const first = formEl.querySelector('input, textarea, select');
  if (first) first.focus();
}

function field(label, input, { wide = true, hint } = {}) {
  // A checkbox list can't sit inside <label> (clicking the title would toggle the first box)
  const group = input.tagName === 'DIV';
  if (group) { input.setAttribute('role', 'group'); input.setAttribute('aria-label', label); }
  return el(group ? 'div' : 'label', { class: `field${wide ? ' field-wide' : ''}` }, label, input, hint ? el('small', { text: hint }) : null);
}

function checklist(items, selected, emptyText) {
  const box = el('div', { class: 'checklist' });
  if (!items.length) box.append(el('div', { class: 'none', text: emptyText }));
  for (const it of items) {
    box.append(el('label', {}, el('input', { type: 'checkbox', value: it.id, checked: selected.includes(it.id) }),
      it.label, it.meta ? el('small', { text: it.meta }) : null));
  }
  box.values = () => [...box.querySelectorAll('input:checked')].map((i) => Number(i.value));
  return box;
}

/** Teams with their projects nested inside; a project can only be checked together with its team. */
function groupTree(teamIds, projectIds) {
  const box = el('div', { class: 'checklist tree' });
  const { teams, projects } = state.org;
  const orphans = projects.filter((p) => !p.team_id || !byId(teams, p.team_id));
  if (!teams.length && !orphans.length) box.append(el('div', { class: 'none', text: 'No teams yet. Create them in the “Teams and projects” card.' }));
  const teamBoxes = new Map();
  const projectBoxes = [];
  for (const t of teams) {
    const tb = el('input', { type: 'checkbox', checked: teamIds.includes(t.id) });
    teamBoxes.set(t.id, tb);
    box.append(el('label', { class: 'tree-team' }, tb, el('strong', { text: t.name })));
    const kids = projects.filter((p) => p.team_id === t.id);
    for (const p of kids) {
      const pb = el('input', { type: 'checkbox', checked: projectIds.includes(p.id) });
      projectBoxes.push([p, pb]);
      pb.addEventListener('change', () => { if (pb.checked) tb.checked = true; });
      box.append(el('label', { class: 'tree-project' }, pb, p.name));
    }
    tb.addEventListener('change', () => {
      if (!tb.checked) projectBoxes.filter(([p]) => p.team_id === t.id).forEach(([, pb]) => { pb.checked = false; });
    });
  }
  if (orphans.length) {
    box.append(el('div', { class: 'tree-label', text: 'Projects without a team' }));
    for (const p of orphans) {
      const pb = el('input', { type: 'checkbox', checked: projectIds.includes(p.id) });
      projectBoxes.push([p, pb]);
      box.append(el('label', { class: 'tree-project' }, pb, p.name));
    }
  }
  box.teamIds = () => [...teamBoxes].filter(([, b]) => b.checked).map(([id]) => id);
  box.projectIds = () => projectBoxes.filter(([, b]) => b.checked).map(([p]) => p.id);
  return box;
}

function employeeForm(emp, reload) {
  const e = emp || { full_name: '', email: '', role: '', aliases: '', active: true, team_ids: [], project_ids: [] };
  const name = el('input', { type: 'text', value: e.full_name, required: true, maxlength: '200', placeholder: 'First Last' });
  const email = el('input', { type: 'email', value: e.email || '', maxlength: '200', placeholder: 'name@company.com' });
  const role = el('input', { type: 'text', value: e.role || '', maxlength: '200', placeholder: 'e.g. Backend developer' });
  const aliases = el('input', { type: 'text', value: e.aliases || '', maxlength: '300', placeholder: 'e.g. Ari, A.K.' });
  const active = el('input', { type: 'checkbox', checked: e.active });
  const groups = groupTree(e.team_ids, e.project_ids);
  openForm(emp ? 'Edit employee' : 'New employee', [
    el('div', { class: 'field-grid' },
      field('Full name', name),
      field('Email', email, { wide: false }),
      field('Role', role, { wide: false }),
      field('Nicknames', aliases, { hint: 'What people call them in meetings, comma separated. Helps assign tasks to the right person.' }),
      field('Teams and projects', groups, { hint: 'Choosing a project also adds the person to its team.' }),
      el('label', { class: 'check-inline field-wide' }, active, 'Active (takes part in meetings)'),
      ),
  ], {
    onSave: async () => {
      const body = JSON.stringify({ full_name: name.value, email: email.value, role: role.value, aliases: aliases.value,
        active: active.checked, team_ids: groups.teamIds(), project_ids: groups.projectIds() });
      await api(emp ? `/api/employees/${emp.id}` : '/api/employees', { method: emp ? 'PUT' : 'POST', body });
      toast(emp ? 'Changes saved.' : 'Employee added.');
      reload();
    },
    onDelete: emp ? async () => {
      if (!confirm(`Delete “${emp.full_name}”? Their tasks stay, but no longer linked to them.`)) return false;
      await api(`/api/employees/${emp.id}`, { method: 'DELETE' });
      toast('Employee deleted.');
      reload();
    } : null,
  });
}

/** kind = 'team' | 'project'. For a new project, `presetTeamId` picks its team. */
function groupForm(kind, group, reload, presetTeamId = null) {
  const g = group || { name: '', description: '', team_id: presetTeamId, member_ids: [] };
  const word = kind === 'team' ? 'team' : 'project';
  const name = el('input', { type: 'text', value: g.name, required: true, maxlength: '120' });
  const desc = el('input', { type: 'text', value: g.description || '', maxlength: '500' });
  const people = (ids) => state.org.employees.filter((e) => e.active || g.member_ids.includes(e.id))
    .filter((e) => !ids || ids.includes(e.id))
    .map((e) => ({ id: e.id, label: e.full_name, meta: e.role }));

  let members;
  let membersSlot = el('div', { class: 'field field-wide' });
  const pmSel = kind === 'project' ? el('select', {}) : null;
  const jiraKey = kind === 'project' ? el('input', { type: 'text', value: g.jira_key || '', maxlength: '10', placeholder: 'e.g. PAY',
    style: { textTransform: 'uppercase' } }) : null;
  const paintMembers = () => {
    if (kind === 'team') {
      members = checklist(people(null), g.member_ids, 'Add employees first.');
    } else {
      const t = byId(state.org.teams, Number(team.value));
      const keep = members ? members.values() : g.member_ids;
      members = checklist(t ? people(t.member_ids) : [], keep,
        t ? `The team “${t.name}” has no members yet. Add them to the team.` : 'Choose the team first.');
    }
    if (pmSel) {
      // The PM is one person from the project's team
      const t = byId(state.org.teams, Number(team.value));
      const keepPm = pmSel.value || (g.pm_id ? String(g.pm_id) : '');
      pmSel.replaceChildren(el('option', { value: '', text: t ? 'No PM' : 'Choose the team first' }),
        (t ? people(t.member_ids) : []).map((p) => el('option', { value: p.id, text: p.label, selected: String(p.id) === keepPm })));
    }
    membersSlot.replaceWith(membersSlot = field('Members', members, {
      hint: kind === 'team'
        ? 'Whoever is removed from the team is also removed from its projects.'
        : 'Only team members can be in the project. The AI knows these names when a meeting is linked to the project.',
    }));
  };

  const team = kind === 'project' ? el('select', { required: true, onchange: paintMembers },
    el('option', { value: '', text: 'Choose the team…' }),
    state.org.teams.map((t) => el('option', { value: t.id, text: t.name, selected: t.id === g.team_id }))) : null;

  const grid = el('div', { class: 'field-grid' },
    field('Name', name, { wide: kind === 'team' }),
    team ? field('Team', team, { wide: false }) : null,
    pmSel ? field('Project manager', pmSel, { wide: false, hint: 'Gets meeting summaries and reminders by email.' }) : null,
    jiraKey ? field('Jira key', jiraKey, { wide: false, hint: 'Where this project’s tickets are created.' }) : null,
    field('Description', desc),
    membersSlot);
  paintMembers();
  openForm(group ? `Edit ${word}` : (kind === 'team' ? 'New team' : 'New project'), [grid], {
    onSave: async () => {
      const body = JSON.stringify({ name: name.value, description: desc.value, team_id: team && team.value ? Number(team.value) : null,
        pm_id: pmSel && pmSel.value ? Number(pmSel.value) : null, jira_key: jiraKey ? jiraKey.value : null, member_ids: members.values() });
      await api(group ? `/api/${kind}s/${group.id}` : `/api/${kind}s`, { method: group ? 'PUT' : 'POST', body });
      toast('Saved.');
      reload();
    },
    onDelete: group ? async () => {
      if (!confirm(`Delete the ${word} “${group.name}”? Employees are not deleted.`)) return false;
      await api(`/api/${kind}s/${group.id}`, { method: 'DELETE' });
      toast('Deleted.');
      reload();
    } : null,
  });
}

function importForm(reload) {
  const text = el('textarea', { placeholder: 'Name, Email, Role, Teams, Projects\nJane Doe, jane@company.com, Developer, Backend, Project A; Project B', spellcheck: false });
  openForm('Import employees', [
    el('p', { class: 'small muted', text: 'One employee per line: Name, Email, Role, Teams, Projects. Separate several teams or projects with semicolons (;). Missing teams and projects are created automatically. If the email or name already exists, the employee is updated, not duplicated.' }),
    field('List (CSV)', text, { hint: 'You can paste it straight from Excel after saving as CSV.' }),
  ], {
    saveText: 'Import',
    onSave: async () => {
      if (!text.value.trim()) throw new Error('The list is empty.');
      const r = await api('/api/employees/import', { method: 'POST', body: JSON.stringify({ csv: text.value }) });
      toast(`${r.created} new, ${r.updated} updated${r.errors.length ? `, ${r.errors.length} errors` : ''}.`);
      reload();
    },
  });
}

async function renderTeam() {
  await loadOrg();
  let filter = { type: 'all', id: null };
  let query = '';
  const reload = async () => { await loadOrg(); paint(); };

  const listCard = el('section', { class: 'card', style: { gap: '4px' } });
  const filterChips = el('div', { class: 'chips' });
  const search = el('input', { type: 'search', class: 'search-input', placeholder: 'Search by name, role or email…', 'aria-label': 'Search employees',
    oninput: () => { query = search.value.toLowerCase(); paintList(); } });
  const orgCard = el('section', { class: 'card', style: { gap: '4px' }, 'aria-label': 'Teams and projects' });

  const visible = () => state.org.employees.filter((e) => {
    if (filter.type === 'team' && !e.team_ids.includes(filter.id)) return false;
    if (filter.type === 'project' && !e.project_ids.includes(filter.id)) return false;
    if (filter.type === 'none' && (e.team_ids.length || e.project_ids.length)) return false;
    return !query || [e.full_name, e.role, e.email, e.aliases].some((x) => (x || '').toLowerCase().includes(query));
  });

  const chip = (label, f) => el('button', {
    type: 'button', class: `chip${filter.type === f.type && filter.id === f.id ? ' active' : ''}`, text: label,
    'aria-pressed': String(filter.type === f.type && filter.id === f.id), onclick: () => { filter = f; paint(); },
  });

  const paintList = () => {
    const people = visible();
    const { teams, projects } = state.org;
    listCard.replaceChildren(
      el('div', { class: 'card-head', style: { marginBottom: '8px' } },
        el('h2', { text: 'Employees' }), el('span', { class: 'mono small muted', text: `${people.length} / ${state.org.employees.length}` })),
      ...(people.length ? people.map((e) => el('div', { class: `person${e.active ? '' : ' inactive'}` },
        el('span', { class: 'avatar', style: { background: personColor(e.id) }, text: initials(e.full_name) }),
        el('div', { class: 'grow' },
          el('span', { class: 'name', text: e.full_name + (e.active ? '' : ' (inactive)') + (e.is_admin ? ' · PM' : '')
            + (state.org.projects.some((p) => p.pm_id === e.id) ? ' · PM' : '') }),
          el('span', { class: 'small subtle', text: [e.role, e.email].filter(Boolean).join(' · ') || '—' }),
          (e.team_ids.length || e.project_ids.length) ? el('div', { class: 'tags' },
            // each team followed by this person's projects in it
            e.team_ids.map((id) => byId(teams, id)).filter(Boolean).map((t) => [
              el('span', { class: 'tag-team', text: t.name }),
              e.project_ids.map((id) => byId(projects, id)).filter((p) => p && p.team_id === t.id)
                .map((p) => el('span', { class: 'tag-project', text: p.name }))]),
            e.project_ids.map((id) => byId(projects, id)).filter((p) => p && !e.team_ids.includes(p.team_id))
              .map((p) => el('span', { class: 'tag-project', text: p.name }))) : null),
        e.open_tasks ? el('a', { class: 'pill soon', href: '#/tasks', text: `${e.open_tasks} task${e.open_tasks === 1 ? '' : 's'}`, title: 'Open tasks' }) : null,
        el('button', { type: 'button', class: 'icon-btn', 'aria-label': `Edit ${e.full_name}`, onclick: () => employeeForm(e, reload) }, icon('edit', 18))))
        : [el('div', { class: 'empty' },
          el('strong', { text: state.org.employees.length ? 'No employees match this filter' : 'No employees yet' }),
          state.org.employees.length ? 'Change the filter or search.' : 'Add them one by one, or import the whole list from CSV.')]),
    );
  };

  const faces = (ids) => el('span', { class: 'faces' }, ids.slice(0, 4).map((id) => {
    const e = byId(state.org.employees, id);
    return e ? el('span', { class: 'face', title: e.full_name, style: { background: personColor(e.id) }, text: initials(e.full_name) }) : null;
  }), ids.length > 4 ? el('span', { class: 'face more', text: `+${ids.length - 4}` }) : null);

  const selectable = (f, ...children) => el('button', {
    type: 'button', class: `org-pick${filter.type === f.type && filter.id === f.id ? ' active' : ''}`,
    'aria-pressed': String(filter.type === f.type && filter.id === f.id),
    onclick: () => { filter = filter.type === f.type && filter.id === f.id ? { type: 'all', id: null } : f; paint(); },
  }, ...children);

  const paintOrg = () => {
    const { teams, projects } = state.org;
    const orphans = projects.filter((p) => !p.team_id || !byId(teams, p.team_id));
    const projectRow = (p) => el('div', { class: 'org-project' },
      selectable({ type: 'project', id: p.id },
        el('span', { class: 'org-name', text: p.name + (p.jira_key ? ` · ${p.jira_key}` : '') }),
        el('span', { class: 'small subtle', text: [p.pm_id && byId(state.org.employees, p.pm_id) ? `PM: ${byId(state.org.employees, p.pm_id).full_name}` : 'no PM',
          `${p.member_ids.length} member${p.member_ids.length === 1 ? '' : 's'}`].join(' · ') })),
      faces(p.member_ids),
      el('button', { type: 'button', class: 'icon-btn', 'aria-label': `Edit project ${p.name}`, onclick: () => groupForm('project', p, reload) }, icon('edit', 16)));
    orgCard.replaceChildren(...nn(
      el('div', { class: 'card-head', style: { marginBottom: '8px' } }, cardTitle('users', 'Teams and projects'),
        el('button', { type: 'button', class: 'btn btn-sm', onclick: () => groupForm('team', null, reload) }, icon('plus', 14, 2.2), 'Team')),
      teams.length ? null : el('div', { class: 'empty' }, el('strong', { text: 'No teams yet' }), 'Create a team, then add its projects.'),
      teams.map((t) => {
        const kids = projects.filter((p) => p.team_id === t.id);
        return el('div', { class: 'org-team' },
          el('div', { class: 'org-team-head' },
            selectable({ type: 'team', id: t.id },
              el('span', { class: 'org-name', text: t.name }),
              el('span', { class: 'small subtle', text: [`${t.member_ids.length} member${t.member_ids.length === 1 ? '' : 's'}`, `${kids.length} project${kids.length === 1 ? '' : 's'}`, t.description].filter(Boolean).join(' · ') })),
            faces(t.member_ids),
            el('button', { type: 'button', class: 'icon-btn', 'aria-label': `Edit team ${t.name}`, onclick: () => groupForm('team', t, reload) }, icon('edit', 16))),
          el('div', { class: 'org-projects' },
            kids.map(projectRow),
            el('button', { type: 'button', class: 'org-add', onclick: () => groupForm('project', null, reload, t.id) }, icon('plus', 14, 2.2), 'Add project')));
      }),
      orphans.length ? el('div', { class: 'org-team' },
        el('div', { class: 'org-team-head' }, el('span', { class: 'org-name subtle', text: 'Projects without a team' })),
        el('p', { class: 'small subtle', text: 'Open them and choose the team they belong to.' }),
        el('div', { class: 'org-projects' }, orphans.map(projectRow))) : null,
    ));
  };

  const paint = () => {
    const { teams, projects, employees } = state.org;
    const activeTeam = filter.type === 'team' ? filter.id : filter.type === 'project' ? (byId(projects, filter.id) || {}).team_id : null;
    filterChips.replaceChildren(...nn(
      chip(`Everyone ${employees.length}`, { type: 'all', id: null }),
      teams.map((t) => [
        chip(`${t.name} ${t.member_ids.length}`, { type: 'team', id: t.id }),
        // a team's projects show up once the team is selected
        t.id === activeTeam ? projects.filter((p) => p.team_id === t.id)
          .map((p) => chip(`↳ ${p.name} ${p.member_ids.length}`, { type: 'project', id: p.id })) : null,
      ]),
      employees.some((e) => !e.team_ids.length && !e.project_ids.length) ? chip('No team', { type: 'none', id: null }) : null));
    paintList();
    paintOrg();
  };

  paint();
  view.replaceChildren(el('div', { class: 'page' },
    pageHead('Organization', 'Team',
      el('button', { type: 'button', class: 'btn', onclick: () => importForm(reload) }, icon('file', 16, 2), 'Import CSV'),
      el('button', { type: 'button', class: 'btn btn-primary', onclick: () => employeeForm(null, reload) }, icon('plus', 16, 2.2), 'Add employee')),
    el('p', { class: 'muted', text: 'Every project belongs to a team and its members come from that team. When a meeting is linked to the team or project, the AI knows the names and tasks are assigned to real people. The data stays on this computer.' }),
    el('div', { class: 'card', style: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: '12px', padding: '16px' } }, search, filterChips),
    el('div', { class: 'row' }, el('div', { class: 'col-3 stack' }, listCard), el('div', { class: 'col-2 stack' }, orgCard))));
}

/* ================= Tickets: review, then send to Jira ================= */

const aiName = () => (state.status.ai_label || 'The AI').split(' · ')[0];

/** Selecting tasks and turning them into ONE ticket. Used on the Tasks page and on each meeting.
 *  reload() refreshes the page after a ticket was made. */
function taskPicker(reload) {
  const selected = new Map();
  const footer = el('div', { class: 'ticket-footer', hidden: true });
  async function make(tasks, btn) {
    if (btn) btn.disabled = true;
    footer.hidden = false;
    footer.replaceChildren(el('div', { class: 'thinking' }, el('span', { class: 'spinner' }),
      state.status.claude ? `${aiName()} is writing the ticket…` : 'Creating the ticket…'));
    try {
      const r = await api('/api/tickets/from-tasks', { method: 'POST', body: JSON.stringify({ task_ids: tasks.map((t) => t.id) }) });
      selected.clear();
      await reload();
      refreshOpenCount();
      openForm('Ticket created', nn(
        el('h3', { class: 'ticket-title', text: r.ticket.description }),
        r.ticket.details ? el('p', { class: 'small muted', style: { whiteSpace: 'pre-line' }, text: r.ticket.details }) : null,
        r.ai ? null : el('p', { class: 'small subtle', text: 'Written from the task text (no AI key).' }),
        el('p', { class: 'small subtle', text: 'Next: on the Tickets page choose who gets it and send it to Jira.' }),
      ), { saveText: 'Go to Tickets', onSave: async () => { location.hash = '#/tickets'; } });
    } catch (e) { toast(e.message); paintFooter(); }
  }
  function paintFooter() {
    const n = selected.size;
    footer.hidden = !n;
    if (!n) return;
    footer.replaceChildren(
      el('span', { class: 'small', text: `${n} task${n === 1 ? '' : 's'} selected` }),
      el('span', { class: 'spacer' }),
      el('button', { type: 'button', class: 'btn', onclick: () => { selected.clear(); reload(); } }, 'Clear'),
      el('button', {
        type: 'button', class: 'btn btn-primary', title: n > 1 ? 'The AI combines the selected tasks into one ticket' : '',
        onclick: (e) => make([...selected.values()], e.currentTarget),
      }, icon('layers', 16, 2), n > 1 ? `Make 1 ticket from ${n} tasks` : 'Make ticket'));
  }
  const pick = {
    selected,
    toggle: (t, on) => { on ? selected.set(t.id, t) : selected.delete(t.id); paintFooter(); },
    single: (t) => make([t]),
  };
  return { pick, footer, paintFooter };
}

/** Edit a task, or the ticket it is part of: title, details, due date, Jira project and the quotes.
 *  quoteTasks: the tasks whose Albanian quotes are shown (they can be fixed and are learned). */
async function ticketEditForm(targetId, quoteTasks, reload) {
  const t = await api(`/api/tasks/${targetId}`);
  if (t.jira_key) { toast(`Already sent to Jira (${t.jira_key}). Change it there.`); return; }
  const isTicket = !!t.is_ticket;
  const title = el('input', { type: 'text', value: t.description, maxlength: '1000', required: true });
  const details = el('textarea', { style: { fontFamily: 'var(--font)', minHeight: '110px' }, rows: Math.min(12, Math.max(3, (t.details || '').split('\n').length + 1)),
    placeholder: 'Details for the person who gets it (optional)' });
  details.value = t.details || '';
  const due = el('input', { type: 'date', value: t.due_date || '' });
  const jiraKey = el('input', { type: 'text', value: t.jira_project || '', maxlength: '10', placeholder: t.ticket_project || state.status.jira_default || 'e.g. PAY',
    style: { textTransform: 'uppercase' } });
  const quotes = quoteTasks.filter((x) => x.quote);
  openForm(isTicket ? 'Edit ticket' : 'Edit task', nn(
    el('div', { class: 'field-grid' },
      field(isTicket ? 'Ticket title' : 'Task', title),
      field('Details', details),
      field('Due date', due, { wide: false, hint: t.due_date_text ? `Said in the meeting: “${t.due_date_text}”` : null }),
      field('Jira project', jiraKey, { wide: false, hint: t.ticket_project && !t.jira_project ? `Empty = ${t.ticket_project} (from the project)` : 'Empty = the meeting’s project' })),
    quotes.length ? el('div', { class: 'stack', style: { gap: '6px' } },
      el('span', { class: 'small', style: { fontWeight: '700' }, text: quotes.length > 1 ? 'What was said (Albanian)' : 'What was said (Albanian)' }),
      el('small', { class: 'subtle', text: 'Double-click a quote (or right-click twice) to fix misheard words. Fixes are learned in Vocabulary and fixed in the transcript too.' }),
      quotes.map((x) => editableQuote(x.id, x.quote, async () => { const fresh = await api(`/api/tasks/${x.id}`); x.quote = fresh.quote; await reload(); }))) : null,
  ), {
    saveText: 'Save',
    onSave: async () => {
      await api(`/api/tasks/${t.id}`, { method: 'PATCH', body: JSON.stringify({
        description: title.value, details: details.value, jira_project: jiraKey.value, due_date: due.value || null, set_due_date: true }) });
      toast(isTicket ? 'Ticket saved.' : 'Task saved.');
      await reload();
    },
    onDelete: isTicket ? async () => {
      if (!confirm('Delete this ticket? It won’t go to Jira. Its tasks stay here.')) return false;
      await api(`/api/tickets/${t.id}`, { method: 'DELETE' });
      toast('Ticket deleted; its tasks are free again.');
      await reload();
    } : null,
  });
}

/** An Albanian quote the PM can fix with a double-click (or two right-clicks).
 *  The fix is learned (Vocabulary) and the same sentence is fixed in the transcript too. */
function editableQuote(taskId, text, onSaved) {
  const p = el('p', { class: 'quote editable', tabindex: '0', title: 'Double-click (or right-click twice) to fix the words', text: `“${text}”` });
  let lastRight = 0;
  const open = () => {
    window.getSelection().removeAllRanges();
    const input = el('textarea', { class: 'line-input', rows: Math.max(2, Math.ceil(text.length / 70)), 'aria-label': 'Fix the quote' });
    input.value = text;
    const cancel = () => { box.replaceWith(p); p.focus(); };
    const save = async () => {
      const value = input.value.trim();
      if (!value || value === text) { cancel(); return; }
      saveBtn.disabled = true;
      try {
        const r = await api(`/api/tasks/${taskId}/quote`, { method: 'PATCH', body: JSON.stringify({ text: value }) });
        const learned = r.learned.map((f) => `“${f.wrong}” → “${f.right}”`).join(', ');
        toast([learned ? `Learned: ${learned}` : 'Quote saved.', r.lines ? `Transcript fixed too.` : ''].filter(Boolean).join(' '));
        await onSaved();
      } catch (e) { toast(e.message); saveBtn.disabled = false; }
    };
    const saveBtn = el('button', { type: 'button', class: 'btn btn-sm btn-primary', onclick: save }, icon('tick', 14, 2.4), 'Save and learn');
    const box = el('div', { class: 'quote-edit' }, input,
      el('div', { class: 'line-edit-actions' },
        el('small', { class: 'subtle', text: 'Enter = save · Esc = cancel · saved to Vocabulary' }),
        el('span', { class: 'spacer' }),
        el('button', { type: 'button', class: 'btn btn-sm', text: 'Cancel', onclick: cancel }),
        saveBtn));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); save(); }
      if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });
    p.replaceWith(box);
    input.focus();
  };
  p.addEventListener('dblclick', open);
  p.addEventListener('contextmenu', (e) => {
    const now = Date.now();
    if (now - lastRight < 600) { e.preventDefault(); lastRight = 0; open(); } else lastRight = now;
  });
  p.addEventListener('keydown', (e) => { if (e.key === 'F2' || e.key === 'Enter') { e.preventDefault(); open(); } });
  return p;
}

async function renderTickets() {
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  let meetingFilter = Number(params.get('meeting')) || null;
  let [all] = await Promise.all([api('/api/tickets'), loadOrg()]);
  const stageOf = (t) => (t.jira_key ? 'sent' : 'send');
  let tab = params.get('tab') === 'sent' ? 'sent' : 'send';
  const selected = new Set();
  const errors = {};
  const tabsBar = el('div', { class: 'filter', role: 'tablist', 'aria-label': 'Tickets' });
  const listSlot = el('div', { class: 'stack' });
  const footer = el('div', { class: 'ticket-footer', hidden: true });

  const reload = async () => { all = await api('/api/tickets'); refreshOpenCount(); paint(); };
  const base = () => all.filter((t) => (!meetingFilter || t.meeting_id === meetingFilter) && (t.status !== 'done' || t.jira_key));
  const visible = () => base().filter((t) => stageOf(t) === tab);

  async function send(tickets) {
    const r = await api('/api/tickets/send', { method: 'POST', body: JSON.stringify({ task_ids: tickets.map((t) => t.id) }) });
    Object.keys(errors).forEach((k) => delete errors[k]);
    Object.assign(errors, r.errors);
    tickets.forEach((t) => selected.delete(t.id));
    const nErr = Object.keys(r.errors).length;
    toast(r.created.length ? `Sent to Jira: ${r.created.join(', ')}${nErr ? ` · ${nErr} not sent` : ''}`
      : `Nothing was sent${nErr ? ' (see the errors on the tickets)' : ''}.`);
    await reload();
  }

  function ticketCard(t) {
    const stage = stageOf(t);
    const head = el('div', { class: 'ticket-head' },
      stage === 'send' ? el('input', {
        type: 'checkbox', class: 'ticket-check', checked: selected.has(t.id), disabled: !t.recipient_email,
        'aria-label': `Select: ${t.description}`, title: t.recipient_email ? 'Select to send' : 'Choose who gets it first',
        onchange: (e) => { e.target.checked ? selected.add(t.id) : selected.delete(t.id); paintFooter(); },
      }) : null,
      el('div', { class: 'grow' },
        el('a', { class: 'small', href: `#/meeting/${t.meeting_id}`, text: `${t.meeting_title} · ${fmtShort(t.meeting_date)}` }),
        t.project_name ? el('span', { class: 'tag-project', text: t.project_name }) : null),
      stage === 'sent' ? el('a', { class: 'jira-link', href: t.jira_url, target: '_blank', rel: 'noopener', text: t.jira_key }) : null);

    const sources = (t.sources || []).length > 1 ? el('details', { class: 'sources' },
      el('summary', { text: `Made from ${t.sources.length} tasks` }),
      el('ul', {}, t.sources.map((x) => el('li', {}, el('span', { text: x.description }), x.quote ? editableQuote(x.id, x.quote, reload) : null)))) : null;
    const body = nn(
      el('h3', { class: 'ticket-title', text: t.description }),
      t.details ? el('p', { class: 'small muted', style: { whiteSpace: 'pre-line' }, text: t.details }) : null,
      t.quote ? (stage === 'sent' ? el('p', { class: 'quote', text: `“${t.quote}”` }) : editableQuote(t.id, t.quote, reload)) : null,
      sources,
      el('p', { class: 'small subtle', text: [
        t.due_date ? `Due: ${t.due_date}` : 'No due date',
        `Jira project: ${t.jira_project || t.ticket_project || state.status.jira_default || '—'}`,
        stage === 'sent' ? `To: ${t.recipient_email || '—'}` : null,
        t.sent_at ? `Sent ${t.sent_at.replace('T', ' ').slice(0, 16)}${t.sent_by_name ? ` by ${t.sent_by_name}` : ''}` : null,
      ].filter(Boolean).join(' · ') }));
    if (stage === 'sent') return el('article', { class: 'card ticket sent' }, head, ...body);

    // Who gets the ticket: an employee from the list, or another work email
    const people = state.org.employees.filter((e) => e.email && (e.active || e.id === t.assignee_id));
    const known = people.find((e) => e.email.toLowerCase() === (t.recipient_email || '').toLowerCase());
    const other = el('input', { type: 'email', value: !known && t.recipient_email ? t.recipient_email : '', placeholder: 'name@anchorzup.com',
      autocomplete: 'off', 'aria-label': 'Other email', hidden: !!known || !t.recipient_email });
    const person = el('select', { 'aria-label': 'Send the ticket to' },
      el('option', { value: '', text: '— Choose a person —', selected: !t.recipient_email }),
      people.map((e) => el('option', { value: e.email, text: `${e.full_name} — ${e.email}`, selected: known && known.id === e.id })),
      el('option', { value: '__other', text: 'Other email…', selected: !known && !!t.recipient_email }));
    const recipient = () => (person.value === '__other' ? other.value.trim() : person.value) || null;
    const saveRecipient = async () => {
      await api(`/api/tasks/${t.id}`, { method: 'PATCH', body: JSON.stringify({ assignee_email: recipient(), set_email: true }) });
    };
    person.addEventListener('change', async () => {
      other.hidden = person.value !== '__other';
      if (person.value === '__other') { other.focus(); return; }
      try { await saveRecipient(); await reload(); } catch (e) { toast(e.message); }
    });
    other.addEventListener('change', async () => { try { await saveRecipient(); await reload(); } catch (e) { toast(e.message); } });

    const sendBtn = el('button', {
      type: 'button', class: 'btn btn-sm btn-primary', disabled: !t.recipient_email || !state.status.jira,
      title: !state.status.jira ? 'Jira is not configured in .env yet' : t.recipient_email ? '' : 'Choose who gets it first',
      onclick: async (e) => {
        if (!confirm(`Send “${t.description}” to Jira for ${t.recipient_email}?`)) return;
        e.currentTarget.disabled = true;
        try { await send([t]); } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
      },
    }, icon('jira', 14, 2.2), 'Send to Jira');

    return el('article', { class: 'card ticket' }, head, ...body,
      el('label', { class: 'field recipient' }, 'Send to', person, other),
      el('p', { class: 'small subtle', text: t.recipient_email
        ? `The Jira ticket is assigned to ${t.recipient_email} and the email goes there.`
        : `Choose who gets this ticket. The list has the people with an email on Settings → Team.` }),
      errors[t.id] ? el('p', { class: 'jira-error', text: errors[t.id] }) : (t.jira_error ? el('p', { class: 'jira-error', text: `Jira: ${t.jira_error}` }) : null),
      el('div', { class: 'ticket-actions' },
        el('a', { class: 'btn btn-sm', href: `#/tasks?edit=${t.id}`, title: 'Change the title, details, due date or quote' }, icon('edit', 14, 2), 'Edit'),
        el('span', { class: 'spacer' }),
        sendBtn));
  }

  function paintFooter() {
    const ready = visible().filter((t) => t.recipient_email);
    const chosen = ready.filter((t) => selected.has(t.id));
    footer.hidden = tab !== 'send' || ready.length < 2;
    if (footer.hidden) return;
    footer.replaceChildren(
      el('label', { class: 'check-inline' },
        el('input', {
          type: 'checkbox', checked: chosen.length === ready.length, 'aria-label': 'Select all with a person',
          onchange: (e) => { ready.forEach((t) => (e.target.checked ? selected.add(t.id) : selected.delete(t.id))); paint(); },
        }), 'Select all'),
      el('span', { class: 'spacer' }),
      el('button', {
        type: 'button', class: 'btn btn-primary', disabled: !chosen.length || !state.status.jira,
        onclick: async (e) => {
          if (!confirm(`Send ${chosen.length} ticket${chosen.length === 1 ? '' : 's'} to Jira? Each person also gets an email.`)) return;
          e.currentTarget.disabled = true;
          try { await send(chosen); } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
        },
      }, icon('jira', 16, 2), `Send selected (${chosen.length})`));
  }

  function paint() {
    const count = (k) => base().filter((t) => stageOf(t) === k).length;
    tabsBar.replaceChildren(...[['send', 'To send'], ['sent', 'Sent']].map(([k, label]) => el('button', {
      type: 'button', role: 'tab', class: tab === k ? 'active' : '', 'aria-selected': String(tab === k),
      text: `${label} ${count(k)}`, onclick: () => { tab = k; paint(); },
    })));
    const shown = visible();
    listSlot.replaceChildren(...nn(
      tab === 'send' && !state.status.jira ? notice('warn', 'jira', el('strong', { text: 'Jira is not configured. ' }),
        'Fill in JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN in .env (or JIRA_MOCK=1 for testing) and restart the server.') : null,
      shown.length ? shown.map(ticketCard)
        : el('section', { class: 'card' }, el('div', { class: 'empty' },
          tab === 'send' ? [el('strong', { text: 'No tickets to send' }), el('span', {}, 'Make tickets by ticking action items in a meeting or on the ', el('a', { href: '#/tasks', text: 'Tasks page' }), '.')]
            : [el('strong', { text: 'No tickets sent yet' }), 'Tickets sent to Jira appear here with their link.']))));
    paintFooter();
  }

  const meeting = meetingFilter && all.find((t) => t.meeting_id === meetingFilter);
  paint();
  view.replaceChildren(el('div', { class: 'page' },
    pageHead('Before they go to Jira', 'Tickets'),
    el('p', { class: 'muted', text: 'Choose who gets each ticket and send it. Nothing goes to Jira unless you send it.' }),
    el('div', { class: 'card-head' }, tabsBar,
      meeting ? el('button', { type: 'button', class: 'chip active', title: 'Clear filter', onclick: () => { meetingFilter = null; location.hash = '#/tickets'; } },
        `Only: ${meeting.meeting_title}`, icon('x', 14, 2.2)) : null),
    listSlot,
    footer));
}

/* ================= Vocabulary (what the system learned) ================= */

async function renderVocabulary() {
  let data = await api('/api/vocabulary');
  const listSlot = el('section', { class: 'card', style: { gap: 0 } });
  const reload = async () => { data = await api('/api/vocabulary'); paint(); };

  function paint() {
    const rows = data.learned;
    listSlot.replaceChildren(...nn(
      el('div', { class: 'card-head' }, el('h2', { text: 'Learned from your fixes' }), el('span', { class: 'mono small muted', text: rows.length })),
      rows.length ? rows.map((c) => el('div', { class: `vocab-row${c.active ? '' : ' is-off'}` },
        el('span', { class: 'wrong', text: c.wrong }),
        icon('arrow', 16, 2),
        el('strong', { class: 'right', text: c.right }),
        el('span', { class: 'grow small subtle', text: `fixed ${c.times}×${c.wrong.length < 3 && c.times < 2 ? ' · applied after the 2nd fix (short word)' : ''}` }),
        el('label', { class: 'check-inline small', title: 'Off = kept but not applied' },
          el('input', { type: 'checkbox', checked: !!c.active, onchange: async (e) => {
            try { await api(`/api/vocabulary/${c.id}`, { method: 'PATCH', body: JSON.stringify({ active: e.target.checked }) }); await reload(); }
            catch (err) { toast(err.message); }
          } }), 'Active'),
        el('button', { type: 'button', class: 'icon-btn', 'aria-label': `Delete ${c.wrong} → ${c.right}`, title: 'Delete', onclick: async () => {
          if (!confirm(`Forget “${c.wrong}” → “${c.right}”?`)) return;
          try { await api(`/api/vocabulary/${c.id}`, { method: 'DELETE' }); toast('Forgotten.'); await reload(); } catch (err) { toast(err.message); }
        } }, icon('trash', 16))))
        : el('div', { class: 'empty' }, el('strong', { text: 'Nothing learned yet' }),
          'Double-click a transcript line or an Albanian quote to fix a misheard word. Every fix is saved here.')));
  }
  paint();
  const glossary = data.glossary || [];
  view.replaceChildren(el('div', { class: 'page' },
    pageHead('Speech recognition', 'Vocabulary'),
    el('p', { class: 'muted', text: 'Every word you fix in a transcript is learned. On the next meetings Whisper listens for the right words, the same mistakes are fixed automatically after transcription, and the AI correction uses them as examples. Switch a correction off if it fixes something it shouldn’t.' }),
    listSlot,
    el('section', { class: 'card' },
      el('div', { class: 'card-head' }, el('h2', { text: 'Team word list' }), el('span', { class: 'mono small muted', text: glossary.length })),
      el('p', { class: 'small subtle', text: 'From data/fjalori.txt: dialect, slang, English work terms and names. Edit that file to change it.' }),
      glossary.length ? el('div', { class: 'chips' }, glossary.map((g) => el('span', { class: 'chip', title: g.meaning || null, text: g.meaning ? `${g.term} = ${g.meaning}` : g.term })))
        : el('p', { class: 'small subtle', text: 'The file is empty.' }))));
}

/* ================= Outbox (emails) ================= */

async function renderOutbox() {
  const list = await api('/api/outbox');
  const STATUS_EMAIL = { sent: ['Sent', 'done'], failed: ['Failed', 'error'], 'dry-run': ['Test, not sent', 'warn'] };
  const KIND = { login: 'Sign-in code', meeting: 'Meeting summary', reminder: 'Reminder', ticket: 'New ticket' };
  const card = el('section', { class: 'card', style: { gap: 0 } },
    list.length ? list.map((o) => el('div', { class: 'outbox-row' },
      el('span', { class: 'badge' }, icon('mail', 18, 2)),
      el('div', { class: 'grow' },
        el('strong', { text: o.subject, style: { color: 'var(--heading)' } }),
        el('span', { class: 'small subtle', text: `${KIND[o.kind] || o.kind || ''} · to ${o.to_email} · ${o.created_at.replace('T', ' ')}` }),
        o.error ? el('span', { class: 'jira-error', text: o.error }) : null,
        el('details', {}, el('summary', { text: 'Show content' }), el('pre', { text: o.body }))),
      el('span', { class: `status ${(STATUS_EMAIL[o.status] || ['', 'work'])[1]}`, text: (STATUS_EMAIL[o.status] || [o.status])[0] })))
      : el('div', { class: 'empty' }, el('strong', { text: 'No emails yet' }), 'Sign-in codes, PM summaries, reminders and ticket notifications appear here.'));
  view.replaceChildren(el('div', { class: 'page' },
    pageHead('Sent emails', 'Notifications',
      el('button', { type: 'button', class: 'btn', onclick: async () => {
        try { const r = await api('/api/reminders/run', { method: 'POST' }); toast(r.sent ? `Sent ${r.sent} reminder${r.sent === 1 ? '' : 's'}.` : 'No reminders for today (or already sent).'); renderOutbox(); }
        catch (e) { toast(e.message); }
      } }, icon('mail', 16, 2), 'Send today’s reminders')),
    state.status.email ? null : notice('warn', 'mail', el('strong', { text: 'Email is not configured. ' }),
      'Nothing is sent out: emails are only kept here. To send them, fill in SMTP_USER and SMTP_PASSWORD (a Google App password) in .env and restart the server.'),
    card));
}

/* ================= Upload / record dialog ================= */

const dlg = document.getElementById('upload-dialog');
const form = document.getElementById('upload-form');
const fileInput = document.getElementById('file-input');
const drop = document.getElementById('drop');
const submitBtn = document.getElementById('upload-submit');
const errBox = document.getElementById('upload-error');
const recBtn = document.getElementById('rec-btn');
const rec = { recorder: null, stream: null, chunks: [], timer: null, started: 0 };
let chosenFile = null;
let mode = 'file';

function setMode(next) {
  mode = next;
  dlg.querySelectorAll('.tab').forEach((t) => { const on = t.dataset.mode === next; t.classList.toggle('active', on); t.setAttribute('aria-selected', String(on)); });
  dlg.querySelectorAll('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane !== next; });
  if (next === 'file') stopRecording(true);
  chosenFile = null;
  updateFileUI();
}

function updateFileUI() {
  const title = document.getElementById('drop-title');
  const sub = document.getElementById('drop-sub');
  if (mode === 'file' && chosenFile) {
    drop.classList.add('has-file');
    title.textContent = chosenFile.name;
    sub.textContent = `${(chosenFile.size / 1048576).toFixed(1)} MB · click to change`;
  } else {
    drop.classList.remove('has-file');
    title.textContent = 'Choose or drop the recording here';
    sub.textContent = 'mp3, wav, m4a, webm… or a .json transcript';
  }
  submitBtn.disabled = !chosenFile;
}

function pickFile(f) {
  if (!f) return;
  chosenFile = f;
  const t = document.getElementById('f-title');
  if (!t.value) t.value = f.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
  updateFileUI();
}

/** Team select + project select (projects grouped by team; a project implies its team). */
function fillMeetingGroups() {
  const teamSel = document.getElementById('f-team');
  const projSel = document.getElementById('f-project');
  // A PM adds meetings only to the projects they manage
  const projects = isAdmin() ? state.org.projects : state.org.projects.filter((p) => state.me.pm_project_ids.includes(p.id));
  const teams = isAdmin() ? state.org.teams : state.org.teams.filter((t) => projects.some((p) => p.team_id === t.id));
  projSel.required = !isAdmin();
  const keepTeam = teamSel.value;
  const paintProjects = () => {
    const tid = Number(teamSel.value) || null;
    const keep = projSel.value;
    const groups = teams.filter((t) => !tid || t.id === tid).map((t) => [t, projects.filter((p) => p.team_id === t.id)]).filter(([, ps]) => ps.length);
    projSel.replaceChildren(
      el('option', { value: '', text: !isAdmin() ? 'Choose the project…' : projects.length ? 'No project' : 'Add on the “Team” page' }),
      groups.map(([t, ps]) => el('optgroup', { label: t.name }, ps.map((p) => el('option', { value: p.id, text: p.name, selected: String(p.id) === keep })))));
  };
  teamSel.replaceChildren(
    el('option', { value: '', text: teams.length ? 'No team' : 'Add on the “Team” page' }),
    teams.map((t) => el('option', { value: t.id, text: t.name, selected: String(t.id) === keepTeam })));
  teamSel.onchange = paintProjects;
  projSel.onchange = () => {
    const p = byId(projects, Number(projSel.value));
    if (p && p.team_id && teamSel.value !== String(p.team_id)) { teamSel.value = String(p.team_id); paintProjects(); projSel.value = String(p.id); }
  };
  paintProjects();
}

function openUpload(startMode = 'file') {
  form.reset();
  errBox.textContent = '';
  document.getElementById('f-date').value = state.today;
  fillMeetingGroups();
  loadOrg().then(fillMeetingGroups).catch(() => {});
  const sel = document.getElementById('f-model');
  sel.replaceChildren(...state.status.models.map((m) => el('option', { value: m, text: m, selected: m === state.status.default_model })));
  document.getElementById('f-model-hint').textContent = state.status.gpu
    ? 'GPU found: large-v3 gives the best accuracy.'
    : '“large-v3” is recommended for Albanian (most accurate). Without a GPU it is slow: a 1-hour meeting can take about 1–2 hours. “large-v3-turbo” is several times faster with slightly lower accuracy. “small” makes many mistakes in Albanian.';
  setMode(startMode);
  dlg.showModal();
}

async function startRecording() {
  try {
    rec.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    errBox.textContent = 'Microphone access was denied. Allow it in the browser and try again.';
    return;
  }
  const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg'].find((t) => MediaRecorder.isTypeSupported(t)) || '';
  rec.recorder = new MediaRecorder(rec.stream, type ? { mimeType: type } : undefined);
  rec.chunks = [];
  rec.recorder.ondataavailable = (e) => e.data.size && rec.chunks.push(e.data);
  rec.recorder.onstop = () => {
    rec.stream.getTracks().forEach((t) => t.stop());
    if (rec.discard) { rec.discard = false; return; }
    const ext = (rec.recorder.mimeType || '').includes('ogg') ? 'ogg' : 'webm';
    chosenFile = new File(rec.chunks, `recording-${state.today}.${ext}`, { type: rec.recorder.mimeType });
    document.getElementById('rec-state').textContent = `Recording saved (${(chosenFile.size / 1048576).toFixed(1)} MB). Press “Start processing”.`;
    submitBtn.disabled = false;
  };
  rec.recorder.start(1000);
  rec.started = Date.now();
  recBtn.classList.add('on');
  recBtn.setAttribute('aria-label', 'Stop recording');
  document.getElementById('rec-state').textContent = 'Recording… press again to stop';
  submitBtn.disabled = true;
  rec.timer = setInterval(() => { document.getElementById('rec-time').textContent = fmtClock((Date.now() - rec.started) / 1000); }, 500);
}

function stopRecording(discard = false) {
  clearInterval(rec.timer);
  recBtn.classList.remove('on');
  recBtn.setAttribute('aria-label', 'Start recording');
  if (rec.recorder && rec.recorder.state !== 'inactive') { rec.discard = discard; rec.recorder.stop(); }
  if (discard) {
    document.getElementById('rec-time').textContent = '00:00';
    document.getElementById('rec-state').textContent = 'Press the button to start recording from the microphone';
  }
}

recBtn.addEventListener('click', () => (rec.recorder && rec.recorder.state === 'recording' ? stopRecording() : startRecording()));
dlg.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => setMode(t.dataset.mode)));
dlg.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => dlg.close()));
dlg.addEventListener('close', () => stopRecording(true));
fileInput.addEventListener('change', () => pickFile(fileInput.files[0]));
['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => pickFile(e.dataTransfer.files[0]));

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!chosenFile) return;
  errBox.textContent = '';
  submitBtn.disabled = true;
  submitBtn.textContent = 'Uploading…';
  const fd = new FormData();
  fd.append('file', chosenFile);
  fd.append('title', document.getElementById('f-title').value);
  fd.append('meeting_date', document.getElementById('f-date').value);
  fd.append('language', document.getElementById('f-lang').value);
  fd.append('model', document.getElementById('f-model').value);
  fd.append('source', mode === 'record' ? 'recording' : 'audio');
  for (const [key, id] of [['team_id', 'f-team'], ['project_id', 'f-project']]) {
    const v = document.getElementById(id).value;
    if (v) fd.append(key, v);
  }
  try {
    const { id } = await api('/api/meetings', { method: 'POST', body: fd });
    dlg.close();
    toast('Meeting added. Processing started.');
    location.hash = `#/meeting/${id}`;
  } catch (err) {
    errBox.textContent = err.message;
    submitBtn.disabled = false;
  } finally {
    submitBtn.textContent = 'Start processing';
  }
});

document.querySelector('[data-action="upload"]').addEventListener('click', () => openUpload('file'));

/* ================= Login ================= */

const loginEl = document.getElementById('login');
const shellEl = document.getElementById('shell');
const loginErr = document.getElementById('login-error');
const emailForm = document.getElementById('login-email-form');
const codeForm = document.getElementById('login-code-form');
let loginEmail = '';

function showLogin() {
  stopPolling();
  state.me = null;
  shellEl.hidden = true;
  loginEl.hidden = false;
  emailForm.hidden = false;
  codeForm.hidden = true;
  loginErr.textContent = '';
  document.getElementById('login-title').textContent = 'Sign in with your work email';
  document.getElementById('login-sub').textContent = 'You’ll get a 6-digit code by email.';
  setTimeout(() => document.getElementById('login-email').focus(), 0);
}

emailForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = emailForm.querySelector('button');
  loginErr.textContent = '';
  btn.disabled = true;
  loginEmail = document.getElementById('login-email').value.trim();
  try {
    const res = await api('/api/auth/request', { method: 'POST', body: JSON.stringify({ email: loginEmail }) });
    emailForm.hidden = true;
    codeForm.hidden = false;
    document.getElementById('login-title').textContent = 'Enter the code';
    document.getElementById('login-sub').textContent = res && res.dev_code
      ? `Test mode (no real email): your code is ${res.dev_code}.`
      : `If ${loginEmail} is registered, the code was sent there. It is valid for 10 minutes.`;
    const code = document.getElementById('login-code');
    code.value = (res && res.dev_code) || '';
    code.focus();
  } catch (err) { loginErr.textContent = err.message; }
  btn.disabled = false;
});

codeForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = codeForm.querySelector('button[type=submit]');
  loginErr.textContent = '';
  btn.disabled = true;
  try {
    await api('/api/auth/verify', { method: 'POST', body: JSON.stringify({ email: loginEmail, code: document.getElementById('login-code').value }) });
    await start();
  } catch (err) { loginErr.textContent = err.message; }
  btn.disabled = false;
});

document.getElementById('login-back').addEventListener('click', showLogin);

document.getElementById('logout').addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* ignore */ }
  location.hash = '#/';
  showLogin();
});

/* ================= Boot ================= */

document.querySelectorAll('[data-icon]').forEach((n) => n.replaceWith(icon(n.dataset.icon, 18, n.closest('.brand-mark') ? 2.2 : 1.9)));

async function start() {
  try {
    state.me = await api('/api/auth/me');
  } catch {
    showLogin();
    return;
  }
  loginEl.hidden = true;
  shellEl.hidden = false;
  document.getElementById('logout').hidden = !!state.me.login_disabled;
  // Role-based navigation
  document.querySelectorAll('[data-role="admin"]').forEach((n) => { n.hidden = !isAdmin(); });
  document.querySelectorAll('[data-role="manager"]').forEach((n) => { n.hidden = !isManager(); });
  document.getElementById('me-name').textContent = state.me.full_name;
  document.getElementById('me-role').textContent = ROLE_LABEL[state.me.role] || state.me.role;
  document.getElementById('me-avatar').textContent = initials(state.me.full_name);
  try {
    await Promise.all([loadStatus(), loadOrg()]);
    await route();
  } catch (e) {
    view.replaceChildren(el('div', { class: 'page' }, notice('error', 'alert', `The server is not responding: ${e.message}`)));
  }
}

start();
