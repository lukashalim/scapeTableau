type Props = {
  path: string | null;
};

export function StatusBar({ path }: Props) {
  if (!path) {
    return <footer className="status-bar">No file selected</footer>;
  }
  const name = path.split(/[/\\]/).pop() || path;
  return (
    <footer className="status-bar" title={path}>
      <strong>{name}</strong>
      <span aria-hidden="true"> · </span>
      <span>{path}</span>
    </footer>
  );
}
