// Touch ID (passkey) setup and removal for the Settings sheet. Vendored, no
// dependencies, no build step. The sign-in page carries its own inline copy of
// the sign-in half, because static files need a session.
//
// window.OneOSPasskey.register(password, code) -> Promise<{ok: true} | {ok: false, error, message}>
// window.OneOSPasskey.remove(id)               -> Promise<{ok: true} | {ok: false, error, message}>
//
// Every register() call asks the server for fresh options: setup needs a fresh
// password + code each time, and options are never cached for a retry.
// Messages are plain text for the caller to show with textContent.
(function () {
  'use strict';

  var CHECK = 'Password or code not accepted. If you just used this code to sign in, wait for the next one.';
  var AGAIN = ' Enter the next code to try again.';

  function toBase64url(buffer) {
    var bytes = new Uint8Array(buffer);
    var binary = '';
    for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function fromBase64url(text) {
    if (typeof text !== 'string' || !/^[A-Za-z0-9_-]*$/.test(text)) throw new TypeError('not base64url');
    var binary = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((text.length + 3) % 4));
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  function supported() {
    return !!(window.PublicKeyCredential && window.navigator && navigator.credentials && window.fetch);
  }

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  }

  function post(path, body, contentType, extra) {
    var init = {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': contentType, 'X-CSRF-Token': csrfToken()},
      body: body
    };
    for (var key in extra || {}) init[key] = extra[key];
    return fetch(path, init);
  }

  function form(values) {
    return new URLSearchParams(values).toString();
  }

  function failure(error, message) {
    return {ok: false, error: error, message: message};
  }

  // The server's options JSON (base64url strings) as the ArrayBuffers
  // navigator.credentials.create() needs. Done here because
  // PublicKeyCredential.parseCreationOptionsFromJSON is not in every browser.
  function creationOptions(options) {
    return {
      rp: {id: options.rp.id, name: options.rp.name},
      user: {id: fromBase64url(options.user.id), name: options.user.name, displayName: options.user.displayName},
      challenge: fromBase64url(options.challenge),
      pubKeyCredParams: options.pubKeyCredParams,
      timeout: options.timeout,
      excludeCredentials: (options.excludeCredentials || []).map(function (item) {
        return {type: item.type, id: fromBase64url(item.id), transports: item.transports};
      }),
      authenticatorSelection: options.authenticatorSelection,
      attestation: options.attestation
    };
  }

  function registrationJSON(credential) {
    var response = credential.response;
    var body = {
      id: credential.id,
      rawId: toBase64url(credential.rawId),
      type: credential.type,
      response: {
        clientDataJSON: toBase64url(response.clientDataJSON),
        attestationObject: toBase64url(response.attestationObject)
      },
      clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {}
    };
    if (typeof response.getTransports === 'function') body.response.transports = response.getTransports();
    if (credential.authenticatorAttachment) body.authenticatorAttachment = credential.authenticatorAttachment;
    return body;
  }

  async function register(password, code) {
    if (!supported()) return failure('unsupported', 'Touch ID passkeys are not available in this browser.');
    try {
      var begun = await post('/settings/passkey/begin', form({password: String(password), code: String(code)}), 'application/x-www-form-urlencoded');
      if (begun.status === 401) {
        var refusal = {};
        try { refusal = await begun.json(); } catch (_) {}
        return failure('check', typeof refusal.message === 'string' && refusal.message ? refusal.message : CHECK);
      }
      if (!begun.ok) return failure('failed', 'Touch ID could not be set up.' + AGAIN);
      var options = creationOptions(await begun.json());
      var credential;
      try {
        credential = await navigator.credentials.create({publicKey: options});
      } catch (error) {
        if (error && error.name === 'InvalidStateError') return failure('exists', 'Touch ID is already set up for OneOS on this Mac.');
        return failure('cancelled', 'Touch ID was cancelled or timed out.' + AGAIN);
      }
      if (!credential) return failure('cancelled', 'Touch ID was cancelled or timed out.' + AGAIN);
      var finished = await post('/settings/passkey/finish', JSON.stringify(registrationJSON(credential)), 'application/json');
      if (!finished.ok) return failure('failed', 'Touch ID could not be set up.' + AGAIN);
      return {ok: true};
    } catch (error) {
      return failure('failed', 'Touch ID could not be set up.' + AGAIN);
    }
  }

  async function remove(id) {
    try {
      // The server answers a removal with a redirect back to this page; do not
      // follow it, reload instead so the sheet shows the new state.
      var response = await post('/settings/passkey/remove', form({id: String(id)}), 'application/x-www-form-urlencoded', {redirect: 'manual'});
      if (response.type === 'opaqueredirect' || response.ok) {
        location.reload();
        return {ok: true};
      }
      return failure('failed', 'Touch ID could not be removed. Reload the page and try again.');
    } catch (error) {
      return failure('failed', 'Touch ID could not be removed. Reload the page and try again.');
    }
  }

  window.OneOSPasskey = {register: register, remove: remove, toBase64url: toBase64url, fromBase64url: fromBase64url, supported: supported};
})();
