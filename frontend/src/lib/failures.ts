/**
 * Which of three things went wrong, for the screen that has to say so.
 *
 * Told apart because each asks something different of the reader: no
 * connection (wait and retry), something no longer there (go back; retrying
 * cannot help), and our own fault (retry later; we are on it). Never the
 * error's own text: it is English, technical, and of no use on a phone.
 */

import { ApiError, NO_RESPONSE } from "@/api/errors";

export type ErrorKind = "offline" | "missing" | "broken";

export function errorKind(error: unknown): ErrorKind {
  if (error instanceof ApiError) {
    if (error.status === NO_RESPONSE) return "offline";
    if (error.status === 404) return "missing";
  }

  // A chunk that cannot be fetched after a deploy, or the browser offline
  // before a request was even made, is a connection problem too.
  if (typeof navigator !== "undefined" && navigator.onLine === false) return "offline";
  if (error instanceof TypeError && /fetch|dynamically imported/i.test(error.message)) {
    return "offline";
  }

  return "broken";
}

export const COPY: Record<ErrorKind, { title: string; body: string }> = {
  offline: {
    title: "Parece que no hay conexión",
    body: "No pudimos hablar con Finflow. Revisa tu internet y vuelve a intentarlo: tus datos están a salvo.",
  },
  missing: {
    title: "Esto ya no está aquí",
    body: "Puede que lo hayas borrado, o que el enlace sea de algo que ya no existe.",
  },
  broken: {
    title: "Algo no salió como esperábamos",
    body: "Ya estamos trabajando en ello. Tus datos están a salvo; intenta de nuevo en un momento.",
  },
};
