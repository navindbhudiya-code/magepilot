<?php
declare(strict_types=1);

namespace Vendor\Faq\Model;

use Magento\Framework\Exception\NoSuchEntityException;
use Vendor\Faq\Api\FaqRepositoryInterface;
use Vendor\Faq\Api\Data\FaqInterface;
use Vendor\Faq\Model\ResourceModel\Faq\CollectionFactory;

class FaqRepository implements FaqRepositoryInterface
{
    public function __construct(
        private readonly CollectionFactory $collectionFactory
    ) {
    }

    public function getById(int $id): FaqInterface
    {
        $collection = $this->collectionFactory->create();
        $collection->addFieldToFilter('entity_id', $id)->setPageSize(1);
        $faq = $collection->getFirstItem();
        if (!$faq->getId()) {
            throw new NoSuchEntityException(__('FAQ with id "%1" does not exist.', $id));
        }
        return $faq;
    }
}
