const trigger = document.querySelector(".search-trigger");

if (trigger) {
  trigger.setAttribute("aria-keyshortcuts", "Control+K Meta+K");
  trigger.querySelector("kbd").hidden = false;
  trigger.addEventListener("click", (event) => {
    const input = document.querySelector("#search input");
    if (input && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
      event.preventDefault();
      input.focus();
    }
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k") {
      event.preventDefault();
      trigger.click();
    }
  });
}
