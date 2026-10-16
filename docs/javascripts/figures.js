// Click a figure to view it full window; Escape, the close button or a
// click outside the drawing closes it. Delegated, so it survives
// Material's instant navigation.
document.addEventListener("click", (event) => {
  const fig = event.target.closest(".ol-fig");
  if (!fig || fig.closest("dialog")) return;
  const dialog = document.createElement("dialog");
  dialog.className = "ol-fig-dialog";
  dialog.setAttribute("aria-label", "Figure, full window");
  const close = document.createElement("button");
  close.type = "button";
  close.className = "ol-fig-close";
  close.textContent = "Close";
  close.addEventListener("click", () => dialog.close());
  dialog.append(close, fig.cloneNode(true));
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
});
