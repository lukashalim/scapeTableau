type Props = {
  path: string | null;
  detail?: string | null;
};

export function StatusBar({ path, detail }: Props) {
  if (!path) {
    return <footer className="status-bar">No file selected</footer>;
  }
  const name = path.split(/[/\\]/).pop() || path;
  return (
    <footer className="status-bar" title={path}>
      <strong>{name}</strong>
      {detail ? (
        <>
          <span aria-hidden="true"> · </span>
          <span>{detail}</span>
        </>
      ) : (
        <>
          <span aria-hidden="true"> · </span>
          <span>{path}</span>
        </>
      )}
    </footer>
  );
}
