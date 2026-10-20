import { useCallback, useState, type DragEvent } from "react";

const ALLOWED = new Set([".hyper", ".twbx", ".tdsx", ".twb"]);

function extensionOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

type Props = {
  busy: boolean;
  onPath: (path: string) => void;
  onInvalid: (message: string) => void;
};

export function DropZone({ busy, onPath, onInvalid }: Props) {
  const [dragging, setDragging] = useState(false);

  const takeFile = useCallback(
    (file: File | undefined) => {
      if (!file) return;
      if (!ALLOWED.has(extensionOf(file.name))) {
        onInvalid("Choose a .twb, .twbx, .hyper, or .tdsx file.");
        return;
      }
      try {
        const path = window.biescape.getPathForFile(file);
        if (!path) {
          onInvalid("Could not read that file path.");
          return;
        }
        onPath(path);
      } catch {
        onInvalid("Could not read that file path.");
      }
    },
    [onInvalid, onPath],
  );

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    if (busy) return;
    takeFile(event.dataTransfer.files?.[0]);
  };

  const browseNative = async () => {
    if (busy) return;
    const path = await window.biescape.selectFile();
    if (path) onPath(path);
  };

  return (
    <div
      className={`dropzone${dragging ? " dragging" : ""}${busy ? " busy" : ""}`}
      role="button"
      tabIndex={0}
      onClick={() => {
        void browseNative();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          void browseNative();
        }
      }}
      onDragEnter={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        setDragging(false);
      }}
      onDrop={onDrop}
    >
      <svg
        className="drop-icon"
        viewBox="0 0 48 48"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <path
          d="M24 30V12"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <path
          d="M16 18l8-8 8 8"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M10 32v4a4 4 0 004 4h20a4 4 0 004-4v-4"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
      </svg>
      <p className="drop-title">Drop .twb, .twbx, .hyper, or .tdsx here</p>
      <button
        type="button"
        className="browse"
        disabled={busy}
        onClick={(e) => {
          e.stopPropagation();
          void browseNative();
        }}
      >
        Browse files
      </button>
    </div>
  );
}
