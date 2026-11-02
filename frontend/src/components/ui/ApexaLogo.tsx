import markSvg from '/apexa-mark.svg?raw'

// One mark for the header, chat and favicon/docs, bundled so avatars do not
// depend on a separately deployed backend route or an image request.
const markSource = `data:image/svg+xml,${encodeURIComponent(markSvg)}`
const LOGO_SIZE = 20

export function ApexaLogo({ decorative = false }: { decorative?: boolean }) {
  return <img src={markSource} width={LOGO_SIZE} height={LOGO_SIZE}
    alt={decorative ? '' : 'APEXA'} aria-hidden={decorative || undefined} className="apexa-brand-mark shrink-0" />
}
