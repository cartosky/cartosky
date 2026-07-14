import { resolveCityLabelMode, type CityLabelMode } from "@/lib/anchor-labels";

export function shouldShowBlockingDiffLoader(params: {
  isLoading: boolean;
  publishedFrameUrl: string | null;
}): boolean {
  return params.isLoading && !params.publishedFrameUrl;
}

export function resolveCompareDiffCityLabelMode(params: {
  leftModel: string;
  variable: string;
}): CityLabelMode {
  return resolveCityLabelMode({
    model: params.leftModel,
    variable: params.variable,
  });
}
