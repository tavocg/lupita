const search = document.querySelector("#search");
const status = document.querySelector("#search-status");

try {
  await import(`${search.dataset.bundlePath}pagefind-component-ui.js`);
  search.hidden = false;
  const query = new URLSearchParams(location.search).get("q")?.trim();
  const input = search.querySelector("input");
  if (query && input) {
    input.value = query;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
  if (location.hash === "#search") search.querySelector("input")?.focus();
} catch {
  status.textContent = "La búsqueda no está disponible en este momento. Inténtalo más tarde.";
  status.hidden = false;
}
