// Drag a card between the master view's four columns (Draft, Waiting, To
// review, Done). Vendored, no dependencies, no build step. Loaded by the home
// page only; it relies on the page's htmx for every request, so a drop is sent
// exactly like a button press (CSRF header from the page's <meta>, the response
// shown in the popup, the minute-refresh trigger honoured).
//
// What a drop does, by where it lands:
//   - the card's own column ........................ nothing
//   - Waiting or To review (any card) .............. opens the card's popup
//   - a bare inbox card or one with a pending
//     classify proposal (data-lane is not "filed") . opens the card's popup
//   - Draft or Done, from a filed card ............. posts /master/stage/{entity}
//     with the card's own path and source hash, the stage, and the browser's date
// The popup is the card's own hx-get, so a drop never decides for the owner
// what the popup asks (a reminder, a destination, a proposal's review hash).
//
// Nothing the server sent is ever written as markup here: request values come
// from data attributes and go to htmx as plain strings, and htmx swaps the
// reply into the popup with its default swap, as it does for the card button.
(function () {
  'use strict';

  var POPUP = '#master-popup';
  var DIRECT = {draft: true, done: true};
  var ASKS = {waiting: true, review: true};
  var TAG = 'application/x-oneos-card';
  // The card as it was when picked up: {entity, path, sha, lane, stage, hxGet}.
  // The page may refresh and rewrite the dragged node mid-drag, so a drop reads
  // only this copy, never the node.
  var dragging = null;
  var busy = false;

  // No hover, a coarse pointer or a narrow screen: no drag at all. The stage
  // switcher and the popup's buttons are the way to move a card there.
  function canDrag() {
    return !!(window.matchMedia && window.matchMedia('(min-width: 768px) and (hover: hover) and (pointer: fine)').matches);
  }

  function closest(node, selector) {
    return node && node.closest ? node.closest(selector) : null;
  }

  function cardOf(node) { return closest(node, '.master-card[data-path]'); }
  function columnOf(node) { return closest(node, '[data-stage]'); }

  function localDay() {
    var d = new Date();
    var pad = function (n) { return String(n).padStart(2, '0'); };
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  function snapshot(card) {
    var own = columnOf(card);
    return {
      entity: card.dataset.entity || '',
      path: card.dataset.path || '',
      sha: card.dataset.sourceSha256 || '',
      lane: card.dataset.lane || '',
      stage: own && own.dataset ? own.dataset.stage : '',
      hxGet: card.getAttribute('hx-get') || '',
    };
  }

  function hasTag(event) {
    var types = event.dataTransfer && event.dataTransfer.types;
    if (!types) return false;
    if (types.contains) return types.contains(TAG);
    return Array.prototype.indexOf.call(types, TAG) >= 0;
  }

  // 'none' | 'popup' | 'post' for this snapshot dropped on this stage.
  function decide(card, stage) {
    if (card.stage === stage) return 'none';
    if (ASKS[stage]) return 'popup';
    if (!DIRECT[stage]) return 'none';
    if (card.lane !== 'filed' || !card.sha) return 'popup';
    return 'post';
  }

  function openPopup(card) {
    if (!card.hxGet) return Promise.resolve();
    return htmx.ajax('GET', card.hxGet, {target: POPUP});
  }

  function post(card, stage) {
    var url = '/master/stage/' + encodeURIComponent(card.entity);
    var values = {
      path: card.path,
      source_sha256: card.sha,
      stage: stage,
      today: localDay(),
    };
    return htmx.ajax('POST', url, {target: POPUP, values: values});
  }

  function clearOver() {
    var over = document.querySelectorAll('.drop-over');
    for (var i = 0; i < over.length; i++) over[i].classList.remove('drop-over');
  }

  function onDragStart(event) {
    var card = cardOf(event.target);
    if (!card) return;
    if (!canDrag()) {
      event.preventDefault();
      return;
    }
    dragging = snapshot(card);
    card.classList.add('is-dragging');
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      // Firefox starts no drag without data. The tag marks a drag that began on
      // a card of this page; nothing else (a file, text, another page) carries it.
      event.dataTransfer.setData(TAG, dragging.entity);
    }
  }

  function onDragOver(event) {
    if (!dragging || !hasTag(event)) return;
    var column = columnOf(event.target);
    if (!column) return;
    if (decide(dragging, column.dataset.stage) === 'none') {
      column.classList.remove('drop-over');
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    column.classList.add('drop-over');
  }

  function onDragLeave(event) {
    var column = columnOf(event.target);
    if (!column) return;
    if (event.relatedTarget && column.contains(event.relatedTarget)) return;
    column.classList.remove('drop-over');
  }

  function onDrop(event) {
    var card = dragging;
    var column = columnOf(event.target);
    clearOver();
    dragging = null;
    if (!card || !hasTag(event) || !column) return;
    event.preventDefault();
    var action = decide(card, column.dataset.stage);
    if (action === 'none' || busy) return;
    busy = true;
    var done = function () { busy = false; };
    var sent = action === 'post' ? post(card, column.dataset.stage) : openPopup(card);
    // A failed send leaves the page as it is; the card's popup shows the note
    // as it now stands.
    Promise.resolve(sent).then(done, function () {
      done();
      return openPopup(card);
    }).catch(function () {});
  }

  function onDragEnd() {
    clearOver();
    var marked = document.querySelectorAll('.is-dragging');
    for (var i = 0; i < marked.length; i++) marked[i].classList.remove('is-dragging');
    dragging = null;
  }

  // Cards carry draggable="true" from the server; turn it off where drag is off.
  function sync() {
    var on = canDrag();
    var cards = document.querySelectorAll('.master-card[data-path]');
    for (var i = 0; i < cards.length; i++) {
      if (on) cards[i].setAttribute('draggable', 'true');
      else cards[i].removeAttribute('draggable');
    }
  }

  document.addEventListener('dragstart', onDragStart);
  document.addEventListener('dragover', onDragOver);
  document.addEventListener('dragleave', onDragLeave);
  document.addEventListener('drop', onDrop);
  document.addEventListener('dragend', onDragEnd);
  document.addEventListener('htmx:afterSwap', sync);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', sync);
  else sync();
  if (window.matchMedia) {
    var query = window.matchMedia('(min-width: 768px) and (hover: hover) and (pointer: fine)');
    if (query.addEventListener) query.addEventListener('change', sync);
  }

  window.OneOSMasterDrag = {decide: decide, sync: sync, canDrag: canDrag};
})();
