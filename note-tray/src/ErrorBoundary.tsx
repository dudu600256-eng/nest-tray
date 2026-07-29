import React, { Component, ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback || (
          <div style={{ padding: 20, color: "#e74c3c" }}>
            <h3>发生错误</h3>
            <pre style={{ fontSize: 12, color: "#aaa" }}>
              {this.state.error?.message}
            </pre>
            <button
              onClick={() => this.setState({ hasError: false })}
              style={{
                marginTop: 8,
                padding: "6px 16px",
                background: "#0f3460",
                color: "#fff",
                border: "none",
                borderRadius: 4,
                cursor: "pointer",
              }}
            >
              重试
            </button>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
