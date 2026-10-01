const dialog = document.querySelector("#search-dialog");
const search = document.querySelector("#search");
const status = document.querySelector("#search-status");
const input = () => search.querySelector("input");
const closeButton = dialog.querySelector(".search-close");

function setQuery(query) {
  const url = new URL(location.href);
  if (query) url.searchParams.set("q", query);
  else url.searchParams.delete("q");
  history.replaceState(null, "", url);
}

function openSearch(query = "") {
  if (!dialog.open) dialog.showModal();
  const field = input();
  if (field && query && field.value !== query) {
    field.value = query;
    field.dispatchEvent(new Event("input", { bubbles: true }));
  }
  requestAnimationFrame(() => field?.focus());
}

for (const trigger of document.querySelectorAll(".search-trigger")) {
  trigger.setAttribute("aria-keyshortcuts", "Control+K Meta+K");
  const shortcutHint = trigger.querySelector("kbd");
  if (shortcutHint) shortcutHint.hidden = false;
}

document.addEventListener("click", (event) => {
  const trigger = event.target.closest(".search-trigger");
  if (!trigger || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  openSearch();
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openSearch();
  }
  if (event.key === "Escape" && dialog.open) dialog.close();
});

dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});

closeButton.addEventListener("click", () => dialog.close());

search.addEventListener("input", () => setQuery(input()?.value.trim() ?? ""));
const params = new URLSearchParams(location.search);

try {
  await import(`${search.dataset.bundlePath}pagefind-component-ui.js`);
  search.hidden = false;
  if (params.has("q") || location.hash === "#search") openSearch(params.get("q")?.trim() ?? "");
} catch {
  status.textContent = "La búsqueda no está disponible en este momento. Inténtalo más tarde.";
  status.hidden = false;
  if (params.has("q") || location.hash === "#search") dialog.showModal();
}
