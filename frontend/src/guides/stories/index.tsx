/**
 * The four flows worth seeing rather than reading.
 *
 * Each one is a thing people get wrong when it is explained in a paragraph:
 * where a movement comes from, why a transfer is not spending, why a loan
 * payment lowers the debt by less than was paid, and when declaring a bill
 * moves money. Every figure is one the app really produces — the loan's is
 * the worked example its own tests hold — so a story never shows a number
 * Finflow would not.
 */

import {
  CalendarDays,
  Check,
  CreditCard,
  Dumbbell,
  HandCoins,
  Landmark,
  Link2,
  Mail,
  PiggyBank,
  Send,
  Store,
  Wallet,
  X,
} from "lucide-react";
import { Logo } from "@/components/Logo";
import { Money } from "@/components/Money";
import type { TutorialSlide } from "@/components/ui/Tutorial";
import {
  Balance,
  Chip,
  Leg,
  Route,
  Share,
  Slip,
  Stage,
  Tile,
} from "@/guides/stories/parts";

export type StoryId = "movimiento" | "traslado" | "cuota" | "factura";

export type Story = {
  id: StoryId;
  /** The question the story answers, as a tab or a button says it. */
  title: string;
  slides: TutorialSlide[];
};

/* ------------------------------------------------------------- movement */

const movement: Story = {
  id: "movimiento",
  title: "Cómo llega un movimiento",
  slides: [
    {
      title: "Pagas con tu tarjeta",
      caption: "$45.000 en el Éxito, con tu tarjeta terminada en 1234.",
      illustration: (
        <Stage>
          <Route>
            <Tile icon={CreditCard} label="Tu tarjeta" tone="cyan" />
            <Leg carries="money" />
            <Tile icon={Store} label="El comercio" tone="violet" at={120} />
          </Route>
          <div className="flex justify-center">
            <Chip label="Pagaste" value="$45.000" tone="accent" at={400} />
          </div>
        </Stage>
      ),
    },
    {
      title: "Tu banco te escribe",
      caption: "Como siempre: un correo a tu Gmail avisando la compra.",
      illustration: (
        <Stage>
          <Route>
            <Tile icon={Landmark} label="Tu banco" tone="cyan" />
            <Leg />
            <Tile icon={Mail} label="Tu Gmail" tone="violet" at={120} />
          </Route>
          <Slip at={350}>
            <p className="text-faint text-xs">De: Bancolombia</p>
            <p className="mt-1">
              Compraste $45.000 en EXITO SUPERINTER CALI con tu T.Cred *1234
            </p>
          </Slip>
        </Stage>
      ),
    },
    {
      title: "Gmail se lo pasa a Finflow",
      caption:
        "Tu filtro reenvía solo los correos de tus bancos. El resto de tu correo no sale de Gmail.",
      illustration: (
        <Stage>
          <Route>
            <Tile icon={Mail} label="Tu Gmail" tone="violet" />
            <Leg />
            <Tile label="Finflow" tone="accent" at={120}>
              <Logo className="size-9" />
            </Tile>
          </Route>
          <div className="flex flex-col gap-2">
            <Slip at={350} className="flex items-center justify-between gap-3 py-2">
              <span>Bancolombia</span>
              <span className="flex items-center gap-1 text-incoming text-xs">
                <Check className="size-3.5" aria-hidden />
                Reenviado
              </span>
            </Slip>
            <Slip at={500} className="flex items-center justify-between gap-3 py-2">
              <span className="text-muted">Tu trabajo, tu familia…</span>
              <span className="flex items-center gap-1 text-faint text-xs">
                <X className="size-3.5" aria-hidden />
                Se queda en Gmail
              </span>
            </Slip>
          </div>
        </Stage>
      ),
    },
    {
      title: "Finflow lo lee",
      caption: "Saca lo que importa del correo, sin que escribas nada.",
      illustration: (
        <Stage>
          <Slip className="text-muted text-xs">
            Compraste $45.000 en EXITO SUPERINTER CALI con tu T.Cred *1234, el 08/10 a
            las 10:15
          </Slip>
          <div className="flex flex-wrap justify-center gap-2">
            <Chip label="Monto" value="$45.000" tone="accent" at={300} />
            <Chip label="Comercio" value="Éxito" tone="violet" at={550} />
            <Chip label="Tarjeta" value="····1234" tone="cyan" at={800} />
            <Chip label="Fecha" value="8 oct, 10:15" tone="green" at={1050} />
          </div>
        </Stage>
      ),
    },
    {
      title: "Cae en su cuenta",
      caption:
        "Si enlazaste la tarjeta ····1234, el gasto entra en su cuenta y mueve el saldo. Si no, queda «sin asignar» hasta que la enlaces.",
      illustration: (
        <Stage>
          <Slip className="flex items-center justify-between gap-3">
            <span className="min-w-0">
              <span className="block">Éxito</span>
              <span className="block text-faint text-xs">8 oct · Mercado</span>
            </span>
            <Money amount="-45000" currency="COP" signed size="sm" />
          </Slip>
          <Balance
            label="Tarjeta Bancolombia"
            caption="Lo que debes"
            from="455000"
            to="500000"
            tone="negative"
            at={300}
          />
        </Stage>
      ),
    },
    {
      title: "Y te avisa",
      caption:
        "Segundos después, por Telegram si lo conectaste, y siempre en la campana de la app.",
      illustration: (
        <Stage>
          <Route>
            <Tile label="Finflow" tone="accent">
              <Logo className="size-9" />
            </Tile>
            <Leg />
            <Tile icon={Send} label="Tu teléfono" tone="cyan" at={120} />
          </Route>
          <Slip at={350} className="font-mono text-xs leading-relaxed">
            Gasto $45.000
            <br />
            COMPRA EN EXITO SUPERINTER CALI
            <br />
            <span className="text-faint">Bancolombia · 08/10 10:15 a. m.</span>
          </Slip>
        </Stage>
      ),
    },
  ],
};

/* ------------------------------------------------------------- transfer */

const transfer: Story = {
  id: "traslado",
  title: "Un traslado no es un gasto",
  slides: [
    {
      title: "Pagas tu tarjeta desde tus ahorros",
      caption:
        "$500.000 salen de tu cuenta y entran a la tarjeta. Si las dos son del mismo banco, Finflow lo ve solo.",
      illustration: (
        <Stage>
          <Route>
            <Tile icon={PiggyBank} label="Ahorros" tone="cyan" />
            <Leg carries="money" />
            <Tile icon={CreditCard} label="Tarjeta" tone="accent" at={120} />
          </Route>
          <div className="flex justify-center">
            <Chip label="Traslado" value="$500.000" tone="violet" at={400} />
          </div>
        </Stage>
      ),
    },
    {
      title: "Se mueven los dos saldos",
      caption:
        "Tienes menos en ahorros y debes menos en la tarjeta: es una sola plata.",
      illustration: (
        <Stage>
          <Balance
            label="Ahorros"
            caption="Lo que tienes"
            from="1500000"
            to="1000000"
            tone="positive"
          />
          <Balance
            label="Tarjeta"
            caption="Lo que debes"
            from="500000"
            to="0"
            tone="negative"
            at={250}
          />
        </Stage>
      ),
    },
    {
      title: "Tu patrimonio no cambia",
      caption:
        "La plata sigue siendo tuya, solo cambió de lado. Por eso no cuenta como gasto ni como ingreso.",
      illustration: (
        <Stage>
          <Slip className="grid grid-cols-[3.5rem_1fr_auto] items-center gap-3 whitespace-nowrap text-xs">
            <span className="text-faint">Antes</span>
            <span>$1.500.000 − $500.000</span>
            <span className="font-medium">$1.000.000</span>
          </Slip>
          <Slip
            at={250}
            className="grid grid-cols-[3.5rem_1fr_auto] items-center gap-3 whitespace-nowrap text-xs"
          >
            <span className="text-faint">Después</span>
            <span>$1.000.000 − $0</span>
            <span className="font-medium">$1.000.000</span>
          </Slip>
          <div className="flex justify-center gap-2">
            <Chip label="Patrimonio" value="igual" tone="green" at={600} />
            <Chip label="Gastos del mes" value="igual" tone="violet" at={800} />
          </div>
        </Stage>
      ),
    },
  ],
};

/* ----------------------------------------------------------------- loan */

const loan: Story = {
  id: "cuota",
  title: "De qué está hecha una cuota",
  slides: [
    {
      title: "Pagas la cuota",
      caption: "$2.000.000 a un crédito que debe $60.000.000.",
      illustration: (
        <Stage>
          <Route>
            <Tile icon={Wallet} label="Tu cuenta" tone="cyan" />
            <Leg carries="money" />
            <Tile icon={HandCoins} label="Tu crédito" tone="accent" at={120} />
          </Route>
          <div className="flex justify-center">
            <Chip label="Cuota" value="$2.000.000" tone="accent" at={400} />
          </div>
        </Stage>
      ),
    },
    {
      title: "Primero se cobra el mes",
      caption:
        "Los intereses y los seguros de ese mes salen primero de lo que pagaste.",
      illustration: (
        <Stage>
          <div className="flex gap-1">
            <Share
              label="Intereses y seguros"
              amount={<Money amount="920622.87" currency="COP" size="sm" />}
              share={0.46}
              tone="accent"
            />
            <Share
              label="Baja la deuda"
              amount={<Money amount="1079377.13" currency="COP" size="sm" />}
              share={0.54}
              tone="cyan"
              at={600}
            />
          </div>
        </Stage>
      ),
    },
    {
      title: "Solo el resto baja la deuda",
      caption: "Por eso pagar $2.000.000 no la deja en $58.000.000.",
      illustration: (
        <Stage>
          <Balance
            label="Tu crédito"
            caption="Lo que debes"
            from="60000000"
            to="58920622.87"
            tone="negative"
          />
          <p
            className="rise text-center text-faint text-xs"
            style={{ animationDelay: "500ms" }}
          >
            No <span className="line-through">$58.000.000</span>
          </p>
        </Stage>
      ),
    },
    {
      title: "Finflow lo escribe por ti",
      caption:
        "Con la tasa, el día de corte y los seguros declarados, cada corte queda como movimientos con nombre. Y el crédito se vigila sin sumar a tu patrimonio.",
      illustration: (
        <Stage>
          <Slip className="flex items-center gap-2">
            <CalendarDays className="size-4 text-accent" aria-hidden />
            Intereses del corte
          </Slip>
          <Slip at={200} className="flex items-center gap-2">
            <CalendarDays className="size-4 text-accent" aria-hidden />
            Seguro de vida
          </Slip>
        </Stage>
      ),
    },
  ],
};

/* ----------------------------------------------------------------- bill */

const bill: Story = {
  id: "factura",
  title: "De previsto a pagado",
  slides: [
    {
      title: "Declaras lo que se cobra solo",
      caption: "El gimnasio, $120.000 cada mes. Declararlo no mueve ningún saldo.",
      illustration: (
        <Stage>
          <Route>
            <Tile icon={Dumbbell} label="Gimnasio" tone="violet" />
            <Leg />
            <Tile icon={CalendarDays} label="Tu mes" tone="cyan" at={120} />
          </Route>
          <div className="flex flex-wrap justify-center gap-2">
            <Chip label="Previsto" value="$120.000" tone="cyan" at={400} />
            <Chip label="Ahorros" value="sin cambios" tone="green" at={600} />
          </div>
        </Stage>
      ),
    },
    {
      title: "Llega el día, y lo marcas pagado",
      caption:
        "Ahí sí se escribe el gasto: baja el saldo y cuenta en el mes. Se puede deshacer.",
      illustration: (
        <Stage>
          <Slip className="flex items-center justify-between gap-3">
            <span className="flex items-center gap-2">
              <Check className="size-4 text-incoming" aria-hidden />
              Gimnasio pagado
            </span>
            <Money amount="-120000" currency="COP" signed size="sm" />
          </Slip>
          <Balance
            label="Ahorros"
            caption="Lo que tienes"
            from="1000000"
            to="880000"
            tone="positive"
            at={300}
          />
        </Stage>
      ),
    },
    {
      title: "O deja que se cobre sola",
      caption:
        "Espera unos días por si tu banco avisa. Si el movimiento ya está, lo enlaza y no escribe nada; si no aparece, lo escribe ella. Nunca se cobra dos veces.",
      illustration: (
        <Stage>
          <Route>
            <Tile icon={Landmark} label="Aviso del banco" tone="cyan" />
            <Leg />
            <Tile icon={Dumbbell} label="Gimnasio" tone="violet" at={120} />
          </Route>
          <div className="flex flex-wrap justify-center gap-2">
            <Chip
              label="Enlazado"
              value={<Link2 className="inline size-3.5" aria-hidden />}
              tone="green"
              at={400}
            />
            <Chip label="Escrito de nuevo" value="nada" tone="violet" at={650} />
          </div>
        </Stage>
      ),
    },
  ],
};

export const STORIES: readonly Story[] = [movement, transfer, loan, bill];

export function storyOf(id: StoryId): Story {
  return STORIES.find((story) => story.id === id) ?? movement;
}
