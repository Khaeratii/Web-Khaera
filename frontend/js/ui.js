export function setLoading(element, loading, label = "Loading...") {
  if (!element) return;
  element.disabled = loading;
  if (loading) {
    element.dataset.originalLabel = element.textContent;
    element.textContent = label;
  } else if (element.dataset.originalLabel) {
    element.textContent = element.dataset.originalLabel;
  }
}
