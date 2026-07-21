import { Component, type ErrorInfo, type ReactNode } from "react";
import { isRecoverableChunkError, markChunkReloadAttempted } from "../lib/chunk-reload";

type RouteErrorBoundaryProps = {
  resetKey: string;
  children: ReactNode;
};

type RouteErrorBoundaryState = {
  hasError: boolean;
};

export class RouteErrorBoundary extends Component<
  RouteErrorBoundaryProps,
  RouteErrorBoundaryState
> {
  state: RouteErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): RouteErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: unknown, _info: ErrorInfo): void {
    if (isRecoverableChunkError(error) && markChunkReloadAttempted()) {
      window.location.reload();
    }
  }

  componentDidUpdate(prevProps: RouteErrorBoundaryProps): void {
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div
        role="alert"
        className="fixed inset-0 z-[100] grid place-items-center bg-[#07111f] px-6 text-white"
      >
        <div className="flex max-w-md flex-col items-center gap-4 text-center">
          <h1 className="text-lg font-semibold text-white">Something went wrong</h1>
          <p className="text-sm text-white/70">
            The app failed to load. This can happen after an update.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-slate-950/25 px-5 py-3 text-sm font-semibold text-white/88 backdrop-blur-sm transition duration-200 hover:border-white/25 hover:bg-white/[0.06]"
          >
            Reload page
          </button>
        </div>
      </div>
    );
  }
}
