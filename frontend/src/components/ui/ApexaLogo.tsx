import markSvg from '/apexa-mark.svg?raw'

// One mark for the header, chat and favicon/docs, bundled so avatars do not
// depend on a separately deployed backend route or an image request.
const markSource = `data:image/svg+xml,${encodeURIComponent(markSvg)}`

export function ApexaLogo({ size = 28, decorative = false }: { size?: number; decorative?: boolean }) {
  return <img src={markSource} width={size} height={size}
    alt={decorative ? '' : 'APEXA'} aria-hidden={decorative || undefined} className="apexa-brand-mark shrink-0" />
}
