"use client";

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

type Toast = { id: number; message: string };

const ToastContext = createContext<(message: string) => void>(() => {});

/** Shows a short confirmation. The provider sits in the locale layout, so a
 *  toast raised just before a client-side navigation survives the move. */
export function useToast() {
  return useContext(ToastContext);
}

const VISIBLE_MS = 4000;

export default function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const lastId = useRef(0);

  const toast = useCallback((message: string) => {
    const id = (lastId.current += 1);
    setToasts((prev) => [...prev, { id, message }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((item) => item.id !== id));
    }, VISIBLE_MS);
  }, []);

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-6 z-60 flex flex-col items-center gap-2 px-4"
      >
        {toasts.map((item) => (
          <p
            key={item.id}
            role="status"
            data-testid="toast"
            className="animate-toast-in max-w-sm rounded-md bg-foreground px-4 py-2 text-sm text-background shadow-lg"
          >
            {item.message}
          </p>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
