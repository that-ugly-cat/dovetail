// A form that has been sent says so, and cannot be sent twice.
//
// Several buttons here start something slow that costs money: a consultation,
// reading the journals with a model, classifying articles. Without this the
// page looks idle while the request is out, and a second click starts a second
// run. The server answers the first request when it is done; this covers the
// time in between.
//
// On submit the button that was pressed gets a spinner and, if it declares one,
// a label for the wait (`data-busy-label`). Every submit button of the form is
// disabled, and a note the button points at (`data-busy-note="id"`) is shown —
// that is where a long wait says how long it is. The spinner is a CSS animation
// and nothing here uses requestAnimationFrame, which does not run in a tab the
// browser is not drawing.
(function () {
  function buttons(form) {
    return form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]');
  }

  function release(form) {
    delete form.dataset.busy;
    form.removeAttribute("aria-busy");
    buttons(form).forEach(function (b) {
      b.disabled = false;
      b.classList.remove("is-busy");
      if (b.dataset.idleLabel !== undefined) {
        b.textContent = b.dataset.idleLabel;
        delete b.dataset.idleLabel;
      }
      var note = b.dataset.busyNote && document.getElementById(b.dataset.busyNote);
      if (note) note.hidden = true;
    });
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if ((form.getAttribute("method") || "get").toLowerCase() !== "post") return;

    // Already on its way: the second click does nothing.
    if (form.dataset.busy) {
      event.preventDefault();
      return;
    }
    form.dataset.busy = "1";
    form.setAttribute("aria-busy", "true");

    var pressed = event.submitter || buttons(form)[0];
    if (pressed) {
      pressed.classList.add("is-busy");
      if (pressed.dataset.busyLabel) {
        pressed.dataset.idleLabel = pressed.textContent;
        pressed.textContent = pressed.dataset.busyLabel;
      }
      var note = pressed.dataset.busyNote && document.getElementById(pressed.dataset.busyNote);
      if (note) note.hidden = false;
    }
    // Disabled a moment later and not here: a control disabled during the
    // submit event is left out of what the browser sends.
    window.setTimeout(function () {
      buttons(form).forEach(function (b) { b.disabled = true; });
    }, 0);
  });

  // Coming back with the Back button restores the page as it was left, busy
  // button included. Nothing is in flight any more, so let go.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll("form[data-busy]").forEach(release);
  });
})();
