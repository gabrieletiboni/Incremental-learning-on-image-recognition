import numpy as np
import matplotlib.pyplot as plt
import sys
import torch
import torchvision
import math
import random

from torch.utils.data import Subset
import torch.nn as nn

#
# L2 Loss
# L2Loss(outputs, targets)
# outputs -> shape BATCH_SIZE x NUM_CLASSES
# targets -> shape BATCH_SIZE x NUM_CLASSES
#
class L2Loss():
    
    # Constructor
    def __init__(self, reduction=None, alpha=1.0):
        default_reduction = 'mean'

        if reduction == None:
            self.reduction = default_reduction
        elif reduction == 'mean': # Mean on batch_size
            self.reduction = 'mean'
        elif reduction == 'hardmean': # Mean on batch_size and also number_of_classes
            self.reduction = 'hardmean'
        elif reduction == 'sum':
            self.reduction = 'sum'
        else:
            self.reduction = default_reduction

        self.alpha = alpha

        return

    # Methods
    def __call__(self, outputs, targets):

        batch_size = outputs.shape[0]
        num_classes = outputs.shape[1]

        losses = torch.zeros((batch_size), dtype=torch.float32).to('cuda')

        for i, (output, target) in enumerate(zip(outputs, targets)):
            losses[i] = torch.sum(self.alpha*torch.square(output-target))
            # print(losses[i], len(output), len(target), output, target)

        if self.reduction == 'mean':
            losses = torch.sum(losses)/batch_size
        elif self.reduction == 'hardmean':
            losses = torch.sum(losses)/batch_size/num_classes
        elif self.reduction == 'sum':
            losses = torch.sum(losses)

        return losses

class iCaRL() :

    def __init__(self, dataset, batch_size=0, K=2000, device='cuda') :
        self.device = device
        self.batch_size = batch_size
        self.dataset = dataset          
        self.K = K # max number of exemplars
        self.exemplars = [list() for i in range(100)]  # list of lists containing indexes of exemplars
        self.means_of_each_class = None  

    def flattened_exemplars(self):
        # trasforma la lista di liste di exemplar
        # return list of indexes of exemplars
        flat_list = []
        for sublist in self.exemplars:
            for item in sublist:
                flat_list.append(item)
        return flat_list

    def get_indexes_from_label(self, label):
        targets = self.dataset.targets
        indexes = []

        for i,target in enumerate(targets):
            if target == label:
                indexes.append(i)

        return indexes

    def construct_exemplars(self, net, s, t, herding=True):
        # dataloader: contains only current classes
        # s = startng labels 
        # t = ending label
        m = math.floor(self.K / t)

        if herding : 
            with torch.no_grad(): 
                for c in range(s, t) :  
                    features_list = []
                    class_mean = self.means_of_each_class[c].detach().cpu().clone().numpy()
                    indexes = self.get_indexes_from_label(c)
                    samples_of_this_class = Subset(self.dataset, indexes)

                    net.train(False)
                    for image, _ in samples_of_this_class :
                        image = image.view(1, image.size(0),image.size(1),image.size(2))
                        image = image.to(self.device)
                        # feature map
                        features = net.feature_map(image) 
                        # normalization
                        features = self.L2_norm(features).data.cpu().numpy()
                        
                        features_list.append(features[0])

                    features_exemplars = []
                    features = np.array(features_list)
                    i_added = []

                    # print('Norm of class_mean:', np.linalg.norm(class_mean))
                    for k in range(m):
                        # print('Starting k:', k)
                        # sum
                        S = np.sum(features_exemplars, axis=0)
                        # print('S:', S)
                        mean_exemplars = 1.0/(k+1) * (features + S)
                        # normalize mean exemplars
                        mean_exemplars = self.L2_norm(torch.tensor(mean_exemplars).to(self.device),numpy=True)

                        # print('Norm of mean_exemplars:', np.linalg.norm(class_mean))
                        # argmin 
                        # i = np.argmin(np.sqrt( np.sum( (class_mean - mean_exemplars)**2, axis=1) ))

                        # argsort : torna vettore di indici ordinati per distanze crescenti 
                        i_vector = np.argsort( np.sqrt( np.sum( (class_mean - mean_exemplars)**2, axis=1) ) )
                        # print('i_vector[:5]:', i_vector[:5])

                        i = 0
                        while i_vector[i] in i_added :
                            i+=1 

                        # print('i added:', i_vector[i])
                        # TODO controllare che non si prendano sempre gli stessi exemplars

                        # i_added.append(i)
                        i_added.append(i_vector[i])

                        # update exemplars
                        # features_exemplars.append(features[i])
                        features_exemplars.append(features[i_vector[i]])
                        # add index to examplers_set
                        # self.exemplars[c].append(indexes[i])
                        self.exemplars[c].append(indexes[i_vector[i]])

        else:
            # iteriamo sulle nuove classi
            for c in range(s, t) :
                indexes = self.get_indexes_from_label(c)
                samples_of_this_class = Subset(self.dataset, indexes)

                samples_of_this_class_python = [(image, indexes[index]) for index,image in enumerate(samples_of_this_class)]
                random.shuffle(samples_of_this_class_python)

                for i in range(m):
                    self.exemplars[c].append(samples_of_this_class_python[i][1])

        return

    def reduce_exemplars(self,s,t):
        # m = target number of exemplars
        m = math.floor(self.K / t)

        for i in range(s) : 
            self.exemplars[i] = self.exemplars[i][:m]

        return

    def L2_norm(self, features, numpy=False): 
        # L2-norm on rows

        #return [feature/torch.sqrt(torch.sum(torch.square(feature))).item() for feature in features]
        features_norm = torch.zeros((features.size(0),features.size(1)), dtype=torch.float32).to(self.device)

        for i,feature in enumerate(features):
            square = torch.square(feature)
            somma = torch.sum(square)
            sqrt = torch.sqrt(somma).item()
            features_norm[i] += feature/sqrt
            #print(feature/sqrt)
        
        if numpy:
            return features_norm.detach().cpu().numpy()

        return features_norm

    def compute_means(self, net, dataloader, ending_label):
        # dataloader = current classes + exemplars
        sums = torch.zeros((ending_label,64), dtype=torch.float64).to(self.device)
        counts = torch.zeros(ending_label, dtype=torch.int32).to(self.device)
        means_of_each_class = torch.zeros((ending_label,64), dtype=torch.float64).to(self.device)

        with torch.no_grad() :
            net.train(False)
            for images,labels in dataloader:
                # Bring data over the device of choice
                images = images.to(self.device)
                labels = labels.to(self.device)
                
                # feature map (custom)
                features = net.feature_map(images)
                # print(features.size()) #should be BATCH_SIZE x 64

                # normalization
                features = self.L2_norm(features)

                for i,sample in enumerate(features):
                    sums[labels[i]] += sample 
                    counts[labels[i]] += 1

            for i,count in enumerate(counts):
                means_of_each_class[i] += sums[i]/float(count)
            
            #print(means_of_each_class)
            
            self.means_of_each_class = self.L2_norm(means_of_each_class)
            #print(self.means_of_each_class[:5,:])
        return

    def CE_L2_loss(self, net, net_old, criterion, images, labels, current_classes, starting_label, ending_label, distillation_weight=1, outputs_normalization='sigmoid'):

        # Classification loss -> CE
        # Distillation loss -> L2

        CE_criterion = nn.CrossEntropyLoss(reduction='sum')
        L2_criterion = L2Loss(reduction='sum', alpha=0.0001)
        softmax = torch.nn.Softmax(dim=-1)

        outputs = net(images)

        batch_size = outputs.shape[0]
        
        if outputs_normalization == 'softmax':
            outputs_normalized = softmax(outputs)
        elif outputs_normalization == 'sigmoid':
            outputs_normalized = torch.sigmoid(outputs)
        else:
            raise RuntimeError('Errore nella scelta outputs_normalization in CE_L2')

        if starting_label == 0:
            loss = CE_criterion(outputs, labels)/batch_size

        else:
            with torch.no_grad():
                net_old.train(False)
                outputs_old = net_old(images)
                # sigmoids_old = torch.sigmoid(outputs_old[:,0:starting_label])
                if outputs_normalization == 'softmax':
                    probabilities_old = softmax(outputs_old)
                elif outputs_normalization == 'sigmoid':
                    probabilities_old = torch.sigmoid(outputs_old)

            ce_loss = CE_criterion(outputs, labels)#/batch_size
            
            test_sigmoid_outputs = softmax(outputs)
            # print('Some initial outputs:', test_sigmoid_outputs[0, labels[0]], test_sigmoid_outputs[1, labels[1]], test_sigmoid_outputs[2, labels[2]])
            for i in range(len(outputs)):
                print('i',i,'- ', outputs[i, labels[i]].item())

            targets = probabilities_old[:, :starting_label].to('cuda')
            dist_loss = L2_criterion(outputs_normalized[:, :starting_label], targets)#/batch_size

            print(f"[CE loss: {ce_loss.item()} | Dist loss: {dist_loss.item()}")

            loss = (ce_loss + (distillation_weight*dist_loss))/batch_size

        return loss

    def bce_loss_with_logits(self, net, net_old, criterion, images, labels, current_classes, starting_label, ending_label, bce_var=2) :

        # Forward pass to the network
        outputs = net(images)
        
        DIV = 1
        if bce_var == 1 :
            # variante 1
            DIV = 1
        elif bce_var == 2 : 
            # variante 2 (default)
            # Così usi già l'informazione che avrai più classi in futuro e cerchi già di adattare la rete con la BCE, incoraggiando un basso output anche nelle classi successive
            ending_label = 100
            #print('Ending label:', ending_label)
        elif bce_var == 3 : 
            # variante 3
            # divide per un fattore costante fin dall'inizio la BCELoss
            DIV = 128*100
            criterion = nn.BCEWithLogitsLoss(reduction='sum')
        else : 
            raise RuntimeError("Scegliere una variante opportuna bce_loss_with_logits\n varianti 1 2 3")

        if starting_label == 0:
            #targets_bce = torch.zeros([self.batch_size, ending_label], dtype=torch.float32)
            targets_bce = torch.zeros([self.batch_size, ending_label], dtype=torch.float32)
            # one hot encoding
            for i in range(self.batch_size):
                targets_bce[i][labels[i]] = 1
            
            targets_bce = targets_bce.to(self.device)

            #loss = criterion(outputs[:, 0:ending_label], targets_bce)
            loss = criterion(outputs[:, 0:ending_label], targets_bce)/DIV
        else:
            # calcoliamo i vecchi output con la vecchia rete
            with torch.no_grad():
                net_old.train(False)
                outputs_old = net_old(images)
                sigmoids_old = torch.sigmoid(outputs_old[:,0:starting_label])

            targets_bce = torch.zeros([self.batch_size, ending_label], dtype=torch.float32)
            for i in range(self.batch_size):
                if labels[i] in current_classes:
                    targets_bce[i][labels[i]] = 1.

                targets_bce[i,0:starting_label] = sigmoids_old[i]

            targets_bce = targets_bce.to(self.device)
            loss = criterion(outputs[:, 0:ending_label], targets_bce)/DIV
        return loss
    
    def eval_model_nme(self, net, test_dataloader, dataset_length, display=True, suffix=''):

        running_corrects = 0
        for images,labels in test_dataloader:
            # Bring data over the device of choice
            images = images.to(self.device)
            labels = labels.to(self.device)

            net.train(False)

            # feature map (custom)
            features = net.feature_map(images)

            # normalization
            features = self.L2_norm(features)

            for i,sample in enumerate(features):
                dots = torch.tensor([torch.dot(mean, sample).data for mean in self.means_of_each_class])
                y_pred = torch.argmax(dots).item()
                if y_pred == labels[i] : 
                    running_corrects+=1

        accuracy_eval = running_corrects / float(dataset_length)

        if display :    
            print('Accuracy on eval NME'+str(suffix)+':', accuracy_eval)

        return accuracy_eval

    def update_representation(self, net, net_old, train_dataloader_cum_exemplars, criterion, optimizer, current_classes, starting_label, ending_label, current_step, bce_var=1, loss_type='bce'):
        FIRST = True
        ###net.train() # Sets module in training mode (lo facciamo già nel main di iCaRL)

        # Iterate over the dataset
        for images, labels in train_dataloader_cum_exemplars :
            # Bring data over the device of choice
            images = images.to(self.device)
            labels = labels.to(self.device)

            optimizer.zero_grad() # Zero-ing the gradients
            
            if loss_type == 'bce':
                loss = self.bce_loss_with_logits(net, net_old, criterion, images, labels, current_classes, starting_label, ending_label, bce_var=bce_var)            
            elif loss_type == 'ce_l2':
                loss = self.CE_L2_loss(net, net_old, criterion, images, labels, current_classes, starting_label, ending_label, distillation_weight=1, outputs_normalization='sigmoid')

            if current_step == 0 and FIRST:
                print('--- Initial loss on train: {}'.format(loss.item()))
                FIRST = False 

            # Compute gradients for each layer and update weights
            loss.backward()  # backward pass: computes gradients
            optimizer.step() # update weights based on accumulated gradients

        return loss