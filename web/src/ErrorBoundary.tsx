import { Component, type ErrorInfo, type ReactNode } from "react";

/** Keeps a crash in one part of the page from blanking the whole app; shows the error so it can be reported. */
export default class ErrorBoundary extends Component<{ children: ReactNode; label?: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[${this.props.label ?? "app"}]`, error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="error" role="alert">
        Terjadi kesalahan saat menampilkan {this.props.label ?? "halaman ini"}: {this.state.error.message}
        <div><button type="button" className="ghost" onClick={() => this.setState({ error: null })}>Coba lagi</button></div>
      </div>
    );
  }
}
