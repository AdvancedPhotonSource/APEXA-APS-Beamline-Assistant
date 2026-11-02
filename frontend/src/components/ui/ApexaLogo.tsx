import markSvg from '/apexa-mark.svg?raw'

// Bundle the same SVG used by the favicon/docs. Header rendering must not
// depend on a separately deployed backend route or an image request.
const markSource = `data:image/svg+xml,${encodeURIComponent(markSvg)}`

export function ApexaLogo({ size = 28 }: { size?: number }) {
  return <img src={markSource} width={size} height={size}
    alt="" aria-hidden="true" className="shrink-0" />
}
