(function () {
  "use strict";

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    var busyLabel = form.getAttribute("data-busy-label");
    if (!busyLabel) {
      return;
    }
    var button = form.querySelector('button[type="submit"]');
    if (button) {
      button.disabled = true;
      button.textContent = busyLabel;
    }
    var status = document.querySelector("[data-busy-status]");
    if (status) {
      status.textContent = "OpenAlex と通信しています…";
    }
  });
})();
