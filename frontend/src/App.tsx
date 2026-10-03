import { useApiVersion } from "./api/queries";

function ApiStatus() {
  const version = useApiVersion();
  if (version.isPending) {
    return <span>Connecting to the API…</span>;
  }
  if (version.isError) {
    return <span role="alert">API unavailable: {version.error.message}</span>;
  }
  return <span>API version {version.data.version}</span>;
}

export function App() {
  return (
    <div className="shell">
      <header className="shell-header">
        <h1>DAWAM</h1>
        <p>Data Analysis &amp; Warehouse Architecture Modeler</p>
      </header>
      <main className="shell-main" />
      <footer className="shell-footer">
        <ApiStatus />
      </footer>
    </div>
  );
}
