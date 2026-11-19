// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** when this changes (the route pathname), a caught error is cleared so a crash in
   *  one tab does not follow the user to the next */
  resetKey: string;
}
interface State { error: Error | null }

// What a lazy section's import() rejects with when its hashed chunk is gone — Chrome/Vite,
// Safari and Firefox word it differently; webpack-style loaders set the error's name.
const CHUNK_FAILURE =
  /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module/i;

/** True when the app was redeployed under an open tab: the old chunk 404s, and React.lazy
 *  caches the rejected promise, so re-rendering in place can only throw the same error. */
function isChunkLoadError(error: Error): boolean {
  return error.name === "ChunkLoadError" || CHUNK_FAILURE.test(error.message);
}

/** Per-route error boundary: one broken section renders a card, never a blank app.
 *  Applied by App.tsx's pageRoute() around every hub shell and every index/tab. */
export default class SectionBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("section failed to render:", error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const stale = isChunkLoadError(error);
    return (
      <div role="alert"
        className="break-words rounded-xl border border-red-900/60 bg-red-950/30 p-4 text-sm text-red-200">
        <div className="mb-1 font-semibold">This section failed to render</div>
        <p className="break-all font-mono text-xs text-red-300/90">{error.message}</p>
        {stale && (
          <p className="mt-2 text-xs text-red-200/90">
            the app was updated — reload to get the new version
          </p>
        )}
        <button
          onClick={stale ? () => window.location.reload() : () => this.setState({ error: null })}
          className="mt-3 min-h-10 rounded-md border border-red-800 px-3 py-1 text-xs hover:bg-red-900/40 sm:min-h-0 sm:px-2">
          {stale ? "Reload page" : "Reload section"}
        </button>
      </div>
    );
  }
}
