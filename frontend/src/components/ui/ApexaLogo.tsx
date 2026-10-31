/** One SVG source for the app mark, favicon and documentation. */
export function ApexaLogo({ size = 28 }: { size?: number }) {
  return <img src={`${import.meta.env?.BASE_URL ?? '/'}apexa-mark.svg`} width={size} height={size}
    alt="" aria-hidden="true" className="shrink-0" />
}
