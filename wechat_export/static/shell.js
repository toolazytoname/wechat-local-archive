function showProductPage(name) {
  const chat = document.getElementById("archive");
  if (chat) chat.classList.toggle("hidden", name !== "chat");
  document.querySelectorAll("[data-page-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.getAttribute("data-page-panel") !== name);
  });
  document.querySelectorAll(".nav-btn[data-page]").forEach((btn) => {
    const on = btn.getAttribute("data-page") === name || (name === "reader" && btn.getAttribute("data-page") === "learning");
    btn.classList.toggle("active", on);
    if (on) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  });
  if (name === "self" && window.renderSelfPanel) window.renderSelfPanel();
  if (name === "friend" && window.renderFriendPanel) window.renderFriendPanel();
  if (name === "learning" && window.renderLearningPanel) window.renderLearningPanel();
  if (name === "settings" && window.renderSettingsPanel) window.renderSettingsPanel();
}

window.showProductPage = showProductPage;

document.querySelectorAll("[data-page]").forEach((btn) => {
  btn.addEventListener("click", () => showProductPage(btn.getAttribute("data-page")));
});
const navExport = document.getElementById("nav-export");
if (navExport) {
  navExport.addEventListener("click", () => {
    showProductPage("chat");
    const open = document.getElementById("open-export");
    if (open) open.click();
  });
}
const identityBtn = document.getElementById("open-identity");
if (identityBtn) identityBtn.addEventListener("click", () => showProductPage("settings"));
