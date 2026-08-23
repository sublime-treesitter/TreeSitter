; Headings are the only symbols worth indexing in Markdown. Deliberately no `@breadcrumb.N` pragma (see the other
; languages' symbols.scm files): that's this plugin's own convention, not a community standard, and headings don't
; need it anyway - nesting (h2 under h1, h3 under that h2, ...) is derived generically from `@definition.h<N>`'s
; number and document order, in `compute_heading_breadcrumbs`.

(atx_heading (atx_h1_marker) heading_content: (inline) @definition.h1)
(atx_heading (atx_h2_marker) heading_content: (inline) @definition.h2)
(atx_heading (atx_h3_marker) heading_content: (inline) @definition.h3)
(atx_heading (atx_h4_marker) heading_content: (inline) @definition.h4)
(atx_heading (atx_h5_marker) heading_content: (inline) @definition.h5)
(atx_heading (atx_h6_marker) heading_content: (inline) @definition.h6)
