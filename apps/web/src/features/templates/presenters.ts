import type {
  TemplateCatalogLoadState,
  TemplateCatalogView,
  TemplateCatalogItem,
} from "./types";

export function templateCatalogView(
  state: TemplateCatalogLoadState,
  itemCount: number,
): TemplateCatalogView {
  if (state === "loading") return "loading";
  if (state === "offline") return "offline";
  if (state === "error") return "failure";
  return itemCount === 0 ? "empty" : "populated";
}

export function templateIdentity(item: TemplateCatalogItem): string {
  return `${item.template_id}@${item.template_version}`;
}

export function templateOptionLabel(item: TemplateCatalogItem): string {
  return item.title;
}
