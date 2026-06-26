import { SingleModelDetailCard } from "@/components/model-guidance/SingleModelDetailCard";
import type { MeteogramResponse } from "@/lib/meteogram-types";

type Props = {
  response: MeteogramResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
  /** Currently selected model (single-select); null until resolved. */
  selectedModel: string | null;
  timezone: string | null;
  /** Location line forwarded to the card's image export. */
  locationText: string;
};

/**
 * Model Detail view mode: single-model compact meteogram card. Model selection
 * lives in the shared Models tab control panel above.
 */
export function ModelDetailView({
  response,
  loading,
  error,
  reload,
  selectedModel,
  timezone,
  locationText,
}: Props) {
  return (
    <SingleModelDetailCard
      response={response}
      model={selectedModel}
      timezone={timezone}
      locationText={locationText}
      isLoading={loading && !response}
      error={error}
      onRetry={reload}
    />
  );
}
