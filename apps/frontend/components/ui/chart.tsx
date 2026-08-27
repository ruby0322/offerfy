"use client"

import * as React from "react"
import * as RechartsPrimitive from "recharts"

import { cn } from "@/lib/utils"

const THEMES = { light: "", dark: ".dark" } as const

export type ChartConfig = Record<
  string,
  {
    label?: React.ReactNode
    icon?: React.ComponentType
  } & ({ color?: string; theme?: never } | { color?: never; theme: Record<keyof typeof THEMES, string> })
>

type ChartContextProps = {
  config: ChartConfig
}

const ChartContext = React.createContext<ChartContextProps | null>(null)

function useChart() {
  const context = React.useContext(ChartContext)
  if (!context) {
    throw new Error("useChart must be used within a <ChartContainer />")
  }
  return context
}

function ChartContainer({
  id,
  className,
  children,
  config,
  ...props
}: React.ComponentProps<"div"> & {
  config: ChartConfig
  children: React.ComponentProps<typeof RechartsPrimitive.ResponsiveContainer>["children"]
}) {
  const uniqueId = React.useId()
  const chartId = `chart-${id ?? uniqueId.replace(/:/g, "")}`

  return (
    <ChartContext.Provider value={{ config }}>
      <div
        data-slot="chart"
        data-chart={chartId}
        className={cn(
          "flex aspect-video justify-center text-xs [&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground [&_.recharts-cartesian-grid_line[stroke='#ccc']]:stroke-border/50 [&_.recharts-curve.recharts-tooltip-cursor]:stroke-border [&_.recharts-dot[stroke='#fff']]:stroke-transparent [&_.recharts-layer]:outline-hidden [&_.recharts-polar-grid_[stroke='#ccc']]:stroke-border [&_.recharts-radial-bar-background-sector]:fill-muted [&_.recharts-rectangle.recharts-tooltip-cursor]:fill-muted [&_.recharts-reference-line_[stroke='#ccc']]:stroke-border [&_.recharts-sector]:outline-hidden [&_.recharts-sector[stroke='#fff']]:stroke-transparent [&_.recharts-surface]:outline-hidden",
          className,
        )}
        {...props}
      >
        <ChartStyle id={chartId} config={config} />
        <RechartsPrimitive.ResponsiveContainer>
          {children}
        </RechartsPrimitive.ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  )
}

const ChartStyle = ({ id, config }: { id: string; config: ChartConfig }) => {
  const colorConfig = Object.entries(config).filter(([, item]) => item.theme ?? item.color)
  if (!colorConfig.length) return null

  return (
    <style
      dangerouslySetInnerHTML={{
        __html: Object.entries(THEMES)
          .map(
            ([theme, prefix]) => `
${prefix} [data-chart=${id}] {
${colorConfig
  .map(([key, itemConfig]) => {
    const color = itemConfig.theme?.[theme as keyof typeof itemConfig.theme] ?? itemConfig.color
    return color ? `  --color-${key}: ${color};` : null
  })
  .join("\n")}
}
`,
          )
          .join("\n"),
      }}
    />
  )
}

const ChartTooltip = RechartsPrimitive.Tooltip

function ChartTooltipContent({
  active,
  payload,
  className,
  hideLabel = false,
  label,
  labelFormatter,
  labelClassName,
  color,
  nameKey,
}: {
  active?: boolean
  payload?: ReadonlyArray<{
    dataKey?: string | number
    name?: string
    value?: unknown
    color?: string
    payload?: Record<string, unknown>
    type?: string
  }>
  className?: string
  hideLabel?: boolean
  label?: unknown
  labelFormatter?: (value: unknown, payload: unknown) => React.ReactNode
  labelClassName?: string
  color?: string
  nameKey?: string
}) {
  const { config } = useChart()

  if (!active || !payload?.length) return null

  const tooltipLabel = hideLabel ? null : labelFormatter ? (
    <div className={cn("font-medium", labelClassName)}>{labelFormatter(label, payload)}</div>
  ) : label ? (
    <div className={cn("font-medium", labelClassName)}>{String(label)}</div>
  ) : null

  return (
    <div
      className={cn(
        "grid min-w-[8rem] items-start gap-1.5 rounded-lg border border-border/50 bg-background px-2.5 py-1.5 text-xs shadow-xl",
        className,
      )}
    >
      {tooltipLabel}
      <div className="grid gap-1.5">
        {payload
          .filter((item) => item.type !== "none")
          .map((item) => {
            const key = `${nameKey ?? item.name ?? item.dataKey ?? "value"}`
            const itemConfig = getPayloadConfigFromPayload(config, item, key)
            const indicatorColor = color ?? item.color
            return (
              <div key={String(item.dataKey ?? item.name ?? key)} className="flex w-full items-center gap-2">
                <div
                  className="h-2.5 w-2.5 shrink-0 rounded-[2px]"
                  style={{ backgroundColor: indicatorColor }}
                />
                <span className="text-muted-foreground">{itemConfig?.label ?? item.name}</span>
                <span className="ml-auto font-mono font-medium tabular-nums">
                  {typeof item.value === "number" ? item.value.toLocaleString() : String(item.value ?? "")}
                </span>
              </div>
            )
          })}
      </div>
    </div>
  )
}

const ChartLegend = RechartsPrimitive.Legend

function ChartLegendContent({
  className,
  payload,
  nameKey,
}: {
  className?: string
  payload?: ReadonlyArray<{
    value?: string
    dataKey?: string | number
    color?: string
    type?: string
    payload?: Record<string, unknown>
  }>
  nameKey?: string
}) {
  const { config } = useChart()
  if (!payload?.length) return null

  return (
    <div className={cn("flex items-center justify-center gap-4 pt-3", className)}>
      {payload
        .filter((item) => item.type !== "none")
        .map((item) => {
          const key = `${nameKey ?? item.dataKey ?? "value"}`
          const itemConfig = getPayloadConfigFromPayload(config, item, key)
          return (
            <div key={String(item.value ?? item.dataKey ?? key)} className="flex items-center gap-1.5">
              <div className="h-2 w-2 shrink-0 rounded-[2px]" style={{ backgroundColor: item.color }} />
              {itemConfig?.label ?? item.value}
            </div>
          )
        })}
    </div>
  )
}

function getPayloadConfigFromPayload(config: ChartConfig, payload: unknown, key: string) {
  if (typeof payload !== "object" || payload === null) {
    return undefined
  }

  const record = payload as Record<string, unknown>
  const nested =
    "payload" in record && typeof record.payload === "object" && record.payload !== null
      ? (record.payload as Record<string, unknown>)
      : undefined

  let configLabelKey = key
  if (typeof record[key] === "string") {
    configLabelKey = record[key] as string
  } else if (nested && typeof nested[key] === "string") {
    configLabelKey = nested[key] as string
  }

  return configLabelKey in config ? config[configLabelKey] : config[key]
}

export { ChartContainer, ChartTooltip, ChartTooltipContent, ChartLegend, ChartLegendContent, ChartStyle }
