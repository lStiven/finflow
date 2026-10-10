/**
 * Where in Gmail each step happens.
 *
 * Gmail's settings tabs are addressable by their hash, the same one the
 * address bar shows on each tab, so a button can land somebody on the right
 * one instead of describing the way there. `u/0` is the first account the
 * browser is signed into — the screens say so wherever having several
 * accounts would put somebody in the wrong one, and each step still names
 * the way there for when a link lands on the inbox instead.
 *
 * Only links. Nothing in Finflow can change a Gmail setting.
 */
const GMAIL = "https://mail.google.com/mail/u/0/";

export const GMAIL_INBOX_URL = `${GMAIL}#inbox`;
/** «Configuración» → «Reenvío y correo POP/IMAP». */
export const GMAIL_FORWARDING_URL = `${GMAIL}#settings/fwdandpop`;
/** «Configuración» → «Filtros y direcciones bloqueadas». */
export const GMAIL_FILTERS_URL = `${GMAIL}#settings/filters`;
