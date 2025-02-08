import arxiv
import datetime

    try:
        search = arxiv.Search(
            query="au:DeepSeek-AI",
            max_results=5,
            sort_by=arxiv.SortCriterion.SubmittedDate
        )

        results = search.results()

        papers = []
        for paper in results:
            papers.append({
                "title": paper.title,
                "authors": [author.name for author in paper.authors],
                "summary": paper.summary,
                "pdf_url": paper.pdf_url,
                "published": str(paper.published)
            })

        print(papers)
