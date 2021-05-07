import numpy as np
import torch
from torch.nn.functional import cross_entropy


class DataSelectionStrategy(object):
    """
    Implementation of Data Selection Strategy class which serves as base class for other
    dataselectionstrategies for general learning frameworks.
    Parameters
        ----------
        trainloader: class
            Loading the training data using pytorch dataloader
        valloader: class
            Loading the validation data using pytorch dataloader
        model: class
            Model architecture used for training
        num_classes: int
            Number of target classes in the dataset
        linear_layer: bool
            If True, we use the last fc layer weights and biases gradients
            If False, we use the last fc layer biases gradients
        loss: class
            PyTorch Loss function
    """

    def __init__(self, trainloader, valloader, model, num_classes, linear_layer, loss, device, args):
        """
        Constructor method
        """
        self.trainloader = trainloader  # assume its a sequential loader.
        self.valloader = valloader
        self.model = model
        self.N_trn = len(trainloader.sampler)
        self.N_val = len(valloader.sampler)
        self.grads_per_elem = None
        self.val_grads_per_elem = None
        self.numSelected = 0
        self.linear_layer = linear_layer
        self.num_classes = num_classes
        self.trn_lbls = None
        self.val_lbls = None
        self.loss = loss
        self.device = device
        self.args = args

    def select(self, budget, model_params, tea_model_params):
        pass

    def ssl_loss(self, ul_weak_data, ul_strong_data, labels=False):
        all_data = torch.cat([ul_weak_data, ul_strong_data], 0)
        forward_func = self.model.forward
        stu_logits, l1 = forward_func(all_data, last=True, freeze=True)
        stu_unlabeled_weak_logits, stu_unlabeled_strong_logits = torch.chunk(stu_logits, 2, dim=0)
        _, l1_strong = torch.chunk(l1, 2, dim=0)

        pseudo_label = torch.softmax(stu_unlabeled_weak_logits.detach() / self.args.T, dim=-1)
        max_probs, targets_u = torch.max(pseudo_label, dim=-1)
        mask = max_probs.ge(self.args.threshold).float()

        if labels:
            if targets_u.ndim == 1:
                return targets_u
            else:
                return targets_u.argmax(dim=1)
        else:
            L_consistency = self.loss(stu_unlabeled_strong_logits, targets_u) * mask
            return L_consistency, stu_unlabeled_strong_logits, l1_strong, targets_u, mask

    def get_labels(self, valid=False):
        for batch_idx, (ul_weak_aug, ul_strong_aug, _) in enumerate(self.trainloader):
            ul_weak_aug, ul_strong_aug = ul_weak_aug.to(self.device), ul_strong_aug.to(self.device)
            targets = self.ssl_loss(ul_weak_data=ul_weak_aug, ul_strong_data=ul_strong_aug, labels=True)
            if batch_idx == 0:
                self.trn_lbls = targets.view(-1, 1)
            else:
                self.trn_lbls = torch.cat((self.trn_lbls, targets.view(-1, 1)), dim=0)
        self.trn_lbls = self.trn_lbls.view(-1)

        if valid:
            for batch_idx, (inputs, targets) in enumerate(self.valloader):
                if batch_idx == 0:
                    self.val_lbls = targets.view(-1, 1)
                else:
                    self.val_lbls = torch.cat((self.val_lbls, targets.view(-1, 1)), dim=0)
            self.val_lbls = self.val_lbls.view(-1)

    def compute_gradients(self, valid=False, batch=False, perClass=False, store_t=False):
        """
        Computes the gradient of each element.

        Here, the gradients are computed in a closed form using CrossEntropyLoss with reduction set to 'none'.
        This is done by calculating the gradients in last layer through addition of softmax layer.

        Using different loss functions, the way we calculate the gradients will change.

        For LogisticLoss we measure the Mean Absolute Error(MAE) between the pairs of observations.
        With reduction set to 'none', the loss is formulated as:

        .. math::
            \\ell(x, y) = L = \\{l_1,\\dots,l_N\\}^\\top, \\quad
            l_n = \\left| x_n - y_n \\right|,

        where :math:`N` is the batch size.


        For MSELoss, we measure the Mean Square Error(MSE) between the pairs of observations.
        With reduction set to 'none', the loss is formulated as:

        .. math::
            \\ell(x, y) = L = \\{l_1,\\dots,l_N\\}^\\top, \\quad
            l_n = \\left( x_n - y_n \\right)^2,

        where :math:`N` is the batch size.
        Parameters
        ----------
        valid: bool
            if True, the function also computes the validation gradients
        batch: bool
            if True, the function computes the gradients of each mini-batch
        perClass: bool
            if True, the function computes the gradients using perclass dataloaders
        """
        if perClass:
            embDim = self.model.get_embedding_dim()
            for batch_idx, ((ul_weak_aug, ul_strong_aug), _) in enumerate(self.pctrainloader):
                ul_weak_aug, ul_strong_aug = ul_weak_aug.to(self.device), ul_strong_aug.to(self.device)
                loss, out, l1, _, _ = self.ssl_loss(ul_weak_data=ul_weak_aug, ul_strong_data=ul_strong_aug)
                loss = loss.sum()
                if batch_idx == 0:
                    l0_grads = torch.autograd.grad(loss, out)[0]
                    if self.linear_layer:
                        l0_expand = torch.repeat_interleave(l0_grads, embDim, dim=1)
                        l1_grads = l0_expand * l1.repeat(1, self.num_classes)
                    if batch:
                        l0_grads = l0_grads.mean(dim=0).view(1, -1)
                        if self.linear_layer:
                            l1_grads = l1_grads.mean(dim=0).view(1, -1)
                else:
                    batch_l0_grads = torch.autograd.grad(loss, out)[0]
                    if self.linear_layer:
                        batch_l0_expand = torch.repeat_interleave(batch_l0_grads, embDim, dim=1)
                        batch_l1_grads = batch_l0_expand * l1.repeat(1, self.num_classes)
                    if batch:
                        batch_l0_grads = batch_l0_grads.mean(dim=0).view(1, -1)
                        if self.linear_layer:
                            batch_l1_grads = batch_l1_grads.mean(dim=0).view(1, -1)
                    l0_grads = torch.cat((l0_grads, batch_l0_grads), dim=0)
                    if self.linear_layer:
                        l1_grads = torch.cat((l1_grads, batch_l1_grads), dim=0)
            torch.cuda.empty_cache()
            if self.linear_layer:
                self.grads_per_elem = torch.cat((l0_grads, l1_grads), dim=1)
            else:
                self.grads_per_elem = l0_grads

            if valid:
                for batch_idx, (inputs, targets) in enumerate(self.pcvalloader):
                    inputs, targets = inputs.to(self.device), targets.to(self.device, non_blocking=True)
                    if batch_idx == 0:
                        out, l1 = self.model(inputs, last=True, freeze=True)
                        loss = cross_entropy(out, targets, reduction='none').sum()
                        l0_grads = torch.autograd.grad(loss, out)[0]
                        if self.linear_layer:
                            l0_expand = torch.repeat_interleave(l0_grads, embDim, dim=1)
                            l1_grads = l0_expand * l1.repeat(1, self.num_classes)
                        if batch:
                            l0_grads = l0_grads.mean(dim=0).view(1, -1)
                            if self.linear_layer:
                                l1_grads = l1_grads.mean(dim=0).view(1, -1)
                    else:
                        out, l1 = self.model(inputs, last=True, freeze=True)
                        loss = cross_entropy(out, targets, reduction='none').sum()
                        batch_l0_grads = torch.autograd.grad(loss, out)[0]
                        if self.linear_layer:
                            batch_l0_expand = torch.repeat_interleave(batch_l0_grads, embDim, dim=1)
                            batch_l1_grads = batch_l0_expand * l1.repeat(1, self.num_classes)
                        if batch:
                            batch_l0_grads = batch_l0_grads.mean(dim=0).view(1, -1)
                            if self.linear_layer:
                                batch_l1_grads = batch_l1_grads.mean(dim=0).view(1, -1)
                        l0_grads = torch.cat((l0_grads, batch_l0_grads), dim=0)
                        if self.linear_layer:
                            l1_grads = torch.cat((l1_grads, batch_l1_grads), dim=0)
                torch.cuda.empty_cache()
                if self.linear_layer:
                    self.val_grads_per_elem = torch.cat((l0_grads, l1_grads), dim=1)
                else:
                    self.val_grads_per_elem = l0_grads
        else:
            embDim = self.model.get_embedding_dim()
            if store_t:
                targets = []
                masks = []
            for batch_idx, ((ul_weak_aug, ul_strong_aug), _) in enumerate(self.trainloader):
                ul_weak_aug, ul_strong_aug = ul_weak_aug.to(self.device), ul_strong_aug.to(self.device)
                if store_t:
                    loss, out, l1, t, m = self.ssl_loss(ul_weak_data=ul_weak_aug, ul_strong_data=ul_strong_aug)
                    targets.append(t)
                    masks.append(m)
                else:
                    loss, out, l1, _, _ = self.ssl_loss(ul_weak_data=ul_weak_aug, ul_strong_data=ul_strong_aug)
                loss = loss.sum()
                if batch_idx == 0:
                    l0_grads = torch.autograd.grad(loss, out)[0]
                    if self.linear_layer:
                        l0_expand = torch.repeat_interleave(l0_grads, embDim, dim=1)
                        l1_grads = l0_expand * l1.repeat(1, self.num_classes)
                    if batch:
                        l0_grads = l0_grads.mean(dim=0).view(1, -1)
                        if self.linear_layer:
                            l1_grads = l1_grads.mean(dim=0).view(1, -1)
                else:
                    batch_l0_grads = torch.autograd.grad(loss, out)[0]
                    if self.linear_layer:
                        batch_l0_expand = torch.repeat_interleave(batch_l0_grads, embDim, dim=1)
                        batch_l1_grads = batch_l0_expand * l1.repeat(1, self.num_classes)
                    if batch:
                        batch_l0_grads = batch_l0_grads.mean(dim=0).view(1, -1)
                        if self.linear_layer:
                            batch_l1_grads = batch_l1_grads.mean(dim=0).view(1, -1)
                    l0_grads = torch.cat((l0_grads, batch_l0_grads), dim=0)
                    if self.linear_layer:
                        l1_grads = torch.cat((l1_grads, batch_l1_grads), dim=0)

            torch.cuda.empty_cache()
            if store_t:
                self.weak_targets = targets
                self.weak_masks = masks
            if self.linear_layer:
                self.grads_per_elem = torch.cat((l0_grads, l1_grads), dim=1)
            else:
                self.grads_per_elem = l0_grads
            if valid:
                for batch_idx, (inputs, targets) in enumerate(self.valloader):
                    inputs, targets = inputs.to(self.device), targets.to(self.device, non_blocking=True)
                    if batch_idx == 0:
                        out, l1 = self.model(inputs, last=True, freeze=True)
                        loss = cross_entropy(out, targets, reduction='none').sum()
                        l0_grads = torch.autograd.grad(loss, out)[0]
                        if self.linear_layer:
                            l0_expand = torch.repeat_interleave(l0_grads, embDim, dim=1)
                            l1_grads = l0_expand * l1.repeat(1, self.num_classes)
                        if batch:
                            l0_grads = l0_grads.mean(dim=0).view(1, -1)
                            if self.linear_layer:
                                l1_grads = l1_grads.mean(dim=0).view(1, -1)
                    else:
                        out, l1 = self.model(inputs, last=True, freeze=True)
                        loss = cross_entropy(out, targets, reduction='none').sum()
                        batch_l0_grads = torch.autograd.grad(loss, out)[0]
                        if self.linear_layer:
                            batch_l0_expand = torch.repeat_interleave(batch_l0_grads, embDim, dim=1)
                            batch_l1_grads = batch_l0_expand * l1.repeat(1, self.num_classes)

                        if batch:
                            batch_l0_grads = batch_l0_grads.mean(dim=0).view(1, -1)
                            if self.linear_layer:
                                batch_l1_grads = batch_l1_grads.mean(dim=0).view(1, -1)
                        l0_grads = torch.cat((l0_grads, batch_l0_grads), dim=0)
                        if self.linear_layer:
                            l1_grads = torch.cat((l1_grads, batch_l1_grads), dim=0)
                torch.cuda.empty_cache()
                if self.linear_layer:
                    self.val_grads_per_elem = torch.cat((l0_grads, l1_grads), dim=1)
                else:
                    self.val_grads_per_elem = l0_grads

    def update_model(self, model_params):
        """
        Update the models parameters

        Parameters
        ----------
        model_params: OrderedDict
            Python dictionary object containing model's parameters
        tea_model_params: OrderedDict
            Python dictionary object containing teacher model's parameters
        """
        self.model.load_state_dict(model_params)
