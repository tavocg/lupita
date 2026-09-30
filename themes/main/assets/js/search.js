const search = document.querySelector("#search");
const status = document.querySelector("#search-status");

try {
  await import(`${search.dataset.bundlePath}pagefind-component-ui.js`);
  search.hidden = false;
  if (location.hash === "#search") search.querySelector("input")?.focus();
} catch {
  status.textContent = "La búsqueda no está disponible en este momento. Inténtalo más tarde.";
  status.hidden = false;
}
